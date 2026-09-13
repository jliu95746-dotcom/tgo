import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function loadStore(api, timeout = 1000) {
  const source = readFileSync(new URL('../src/stores/deviceSessionStore.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, AbortController, Error, Promise,
    setTimeout: (cb, ms) => setTimeout(cb, Math.min(ms, timeout)), clearTimeout,
    require: (name) => {
      if (name === '@/services/deviceControlApi') return api;
      if (name === 'i18next') return { t: (key) => key };
      return require(name);
    },
  });
  const store = exports.useDeviceSessionStore;
  store.getState().reset('project-a');
  return store;
}

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

test('failed list requests stop loading and remain distinct from empty success', async () => {
  let fail = true;
  const store = loadStore({ listDeviceSessions: async () => {
    if (fail) throw new Error('private exception');
    return { sessions: [], total: 0 };
  } });
  await store.getState().loadSessions();
  assert.equal(store.getState().isListLoading, false);
  assert.equal(store.getState().listError, 'deviceControl.sessions.loadFailed');
  fail = false;
  await store.getState().loadSessions();
  assert.equal(store.getState().listError, null);
});

test('switching device discards an older response even if abort is ignored', async () => {
  const slow = deferred();
  const store = loadStore({ listDeviceSessions: (params) => params.device_id === 'old'
    ? slow.promise : Promise.resolve({ sessions: [{ id: 'new' }], total: 1 }) });
  const first = store.getState().loadSessions(0, 'old');
  await store.getState().loadSessions(0, 'new');
  slow.resolve({ sessions: [{ id: 'old' }], total: 1 });
  await first;
  assert.equal(store.getState().sessions[0].id, 'new');
  assert.equal(store.getState().deviceId, 'new');
});

test('switching sessions never displays another session detail', async () => {
  const slow = deferred();
  const store = loadStore({ getDeviceSession: (id) => id === 'old'
    ? slow.promise : Promise.resolve({ id, status: 'completed', steps: [], step_total: 0 }) });
  const first = store.getState().selectSession('old');
  await store.getState().selectSession('new');
  slow.resolve({ id: 'old', status: 'running', steps: [], step_total: 0 });
  await first;
  assert.equal(store.getState().detail.id, 'new');
});

test('closing or changing account clears data and invalidates pending requests', async () => {
  const slow = deferred();
  const store = loadStore({ listDeviceSessions: () => slow.promise });
  const request = store.getState().loadSessions();
  store.getState().reset('project-b');
  slow.resolve({ sessions: [{ id: 'project-a' }], total: 1 });
  await request;
  assert.equal(store.getState().ownerProjectId, 'project-b');
  assert.equal(store.getState().sessions.length, 0);
  assert.equal(store.getState().isListLoading, false);
});

test('a hanging request is bounded and aborts transport', async () => {
  let signal;
  const store = loadStore({ listDeviceSessions: (_params, options) => {
    signal = options.signal;
    return new Promise(() => {});
  } }, 20);
  await store.getState().loadSessions();
  assert.equal(signal.aborted, true);
  assert.equal(store.getState().isListLoading, false);
  assert.ok(store.getState().listError);
});

test('detail pagination uses the requested offset and preserves interrupted status', async () => {
  const calls = [];
  const store = loadStore({ getDeviceSession: async (id, params) => {
    calls.push(params);
    return { id, status: 'interrupted', steps: [], step_total: 120 };
  } });
  await store.getState().selectSession('owned');
  await store.getState().loadDetail(1);
  assert.equal(calls[1].step_skip, 100);
  assert.equal(calls[1].step_limit, 100);
  assert.equal(store.getState().detail.status, 'interrupted');
});

test('malformed success responses become visible errors rather than broken state', async () => {
  const store = loadStore({ listDeviceSessions: async () => ({}), getDeviceSession: async (id) => ({ id }) });
  await store.getState().loadSessions();
  assert.ok(store.getState().listError);
  assert.equal(store.getState().sessions.length, 0);
  await store.getState().selectSession('owned');
  assert.ok(store.getState().detailError);
  assert.equal(store.getState().detail, null);
});
