import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const id = '11111111-1111-4111-8111-111111111111';
const url = `https://untrusted.example/v1/visitors/${id}/avatar?v=current.png&t=123`;
function service(apiClient) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/visitorAvatar.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, URL, atob, window: { location: { origin: 'https://company.example' } }, require: () => ({ apiClient }) });
  return exports;
}

test('private avatar locator sends only canonical path/version to configured API', async () => {
  const calls = [];
  const apiClient = { getToken: () => 'original', getPrivateImage: async (...args) => { calls.push(args); return 'image'; } };
  const avatar = service(apiClient);
  assert.equal(avatar.privateVisitorAvatarPath(url), `/v1/visitors/${id}/avatar?v=current.png`);
  assert.equal(await avatar.readVisitorAvatar(url, 'original'), 'image');
  assert.equal(calls[0][0].includes('untrusted.example'), false);
  assert.equal(calls[0][1], 'original');
  assert.equal(avatar.privateVisitorAvatarPath('https://public.example/photo.png'), null);
});

test('private avatar ignores link credentials and rejects malformed versions', () => {
  const avatar = service({});
  assert.equal(avatar.privateVisitorAvatarPath(`/v1/visitors/${id}/avatar?token=secret&file_token=wrong`), `/v1/visitors/${id}/avatar`);
  for (const version of ['../x.png', 'one.png&v=two.png', '%2e%2e%2fsecret.png']) {
    assert.equal(avatar.privateVisitorAvatarPath(`/v1/visitors/${id}/avatar?v=${version}`), '');
  }
});

test('changed account discards delayed avatar bytes', async () => {
  let token = 'original';
  const avatar = service({ getToken: () => token, getPrivateImage: async () => { token = 'other'; return 'old-image'; } });
  await assert.rejects(avatar.readVisitorAvatar(url, 'original'), /session changed/);
});

test('missing account cannot fetch a private avatar', async () => {
  let calls = 0;
  const avatar = service({ getToken: () => null, getPrivateImage: async () => { calls++; } });
  await assert.rejects(avatar.readVisitorAvatar(url, null));
  assert.equal(calls, 0);
});

test('image display deadline follows the original access token', () => {
  const avatar = service({});
  const token = 'header.' + Buffer.from(JSON.stringify({ exp: 1234 })).toString('base64url') + '.signature';
  assert.equal(avatar.avatarSessionDeadline(token), 1_234_000);
  assert.equal(avatar.avatarSessionDeadline(null), 0);
  assert.equal(avatar.avatarSessionDeadline('invalid'), 0);
});

function binaryClient(fetch, prepare) {
  const values = new Map([['tgo-auth-token', 'original']]);
  const storage = { getItem: key => values.get(key) ?? null, setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8').replaceAll('import.meta.env', 'testEnv'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, fetch, localStorage: storage, sessionStorage: storage, testEnv: {},
    window: { ENV: { VITE_API_BASE_URL: 'https://configured.example' } }, require: () => ({ language: 'zh' }) });
  if (prepare) exports.setSessionRequestHandler(() => prepare(exports.apiClient));
  return exports.apiClient;
}

test('binary transport uses original header, no URL credentials, redirect or browser cache', async () => {
  let request;
  const api = binaryClient(async (url, options) => {
    request = { url, ...options };
    return { ok: true, headers: new Headers(), blob: async () => new Blob(['image'], { type: 'image/png' }) };
  });
  assert.equal((await api.getPrivateImage(`/v1/visitors/${id}/avatar`, 'original')).size, 5);
  assert.equal(request.url, `https://configured.example/v1/visitors/${id}/avatar`);
  assert.equal(request.headers.Authorization, 'Bearer original');
  assert.equal(request.credentials, 'omit');
  assert.equal(request.cache, 'no-store');
  assert.equal(request.redirect, 'error');
});

test('session renewal cannot replace the captured avatar actor before dispatch', async () => {
  let calls = 0;
  const api = binaryClient(async () => { calls++; }, client => client.setToken('replacement'));
  await assert.rejects(api.getPrivateImage(`/v1/visitors/${id}/avatar`, 'original'), /session changed/);
  assert.equal(calls, 0);
});

test('binary transport discards bytes after token change or non-image response', async () => {
  let api;
  api = binaryClient(async () => ({ ok: true, headers: new Headers(), blob: async () => {
    api.setToken('other'); return new Blob(['old'], { type: 'image/png' });
  } }));
  await assert.rejects(api.getPrivateImage(`/v1/visitors/${id}/avatar`, 'original'), /session changed/);
  const invalid = binaryClient(async () => ({ ok: true, headers: new Headers(), blob: async () => new Blob(['<html>'], { type: 'text/html' }) }));
  await assert.rejects(invalid.getPrivateImage(`/v1/visitors/${id}/avatar`, 'original'), /Invalid avatar image/);
});

function hookHarness() {
  const token = 'h.' + Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 })).toString('base64url') + '.s';
  let auth = { token, user: { id: 'first' }, isAuthenticated: true };
  let state = null, effect, cleanup, dependencies;
  const pending = [], revoked = [], timers = new Map(), events = new Map();
  let timerId = 0;
  const urls = { createObjectURL: () => 'blob:private', revokeObjectURL: value => revoked.push(value) };
  const react = { useState: () => [state, value => { state = value; }], useEffect: (callback, next) => {
    if (!dependencies || next.some((value, i) => value !== dependencies[i])) { effect = callback; dependencies = next; }
  } };
  const exported = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/hooks/useVisitorAvatar.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: exported, URL: urls, Date, AbortController,
    window: { setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, clearTimeout: key => timers.delete(key),
      setInterval: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, clearInterval: key => timers.delete(key),
      addEventListener: (name, fn) => events.set('window:' + name, fn), removeEventListener: name => events.delete('window:' + name) },
    document: { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) },
    require: name => name === 'react' ? react : name.endsWith('authStore') ? { useAuthStore: selector => selector(auth) }
      : name.endsWith('/api') ? { apiClient: { getToken: () => auth.token } } : {
        avatarSessionDeadline: value => value ? Date.now() + 60_000 : 0,
        privateVisitorAvatarPath: () => '/v1/visitors/' + id + '/avatar',
        readVisitorAvatar: () => new Promise(resolve => pending.push(resolve)),
      },
  });
  return { render: () => exported.useVisitorAvatar(url), effects: () => { if (effect) { cleanup?.(); cleanup = effect(); effect = null; } },
    switchAccount: () => { auth = { token: null, user: null, isAuthenticated: false }; },
    finish: () => pending.shift()(new Blob(['image'], { type: 'image/png' })), revoked, timers,
    cleanup: () => cleanup?.() };
}

test('avatar hook masks logout before effects and releases displayed blob on cleanup', async () => {
  const hook = hookHarness();
  assert.equal(hook.render(), '');
  hook.effects(); hook.finish(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(hook.render(), 'blob:private');
  hook.switchAccount();
  assert.equal(hook.render(), '');
  hook.effects();
  assert.deepEqual(hook.revoked, ['blob:private']);
  hook.cleanup();
  assert.equal(hook.timers.size, 0);
});

test('avatar hook never displays a request completing after logout', async () => {
  const hook = hookHarness();
  hook.render(); hook.effects(); hook.switchAccount();
  assert.equal(hook.render(), '');
  hook.effects(); hook.finish(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(hook.render(), '');
  assert.equal(hook.revoked.length, 0);
  hook.cleanup();
});
