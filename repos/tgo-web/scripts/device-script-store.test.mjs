import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const load = (path, dependencies) => {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/' + path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, AbortController, Number, RegExp, require: name => dependencies[name] ?? require(name) });
  return exports;
};
class APIError extends Error { constructor(status) { super('fixture'); this.status = status; } }
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const device = id => ({ id, project_id: 'project', status: 'online' });
const item = { script_id: 'daily_report', grant_digest: 'a'.repeat(64), effect: 'read_only', timeout_seconds: 20, max_output_bytes: 4096, max_runs: 2 };
const local = id => ({ device_id: id, project_id: 'project', connection_instance_id: 'c'.repeat(32), session: { protocol_version: 1, state: 'awaiting_policy', session_id: 'd'.repeat(32) } });
const form = { scriptId: 'daily_report', timeoutSeconds: 10, maxOutputBytes: 4096, maxRuns: 1 };

function fixture(overrides = {}) {
  let user = { id: 'admin', project_id: 'project', role: 'admin' };
  const listeners = [];
  const calls = [];
  const api = {
    getDeviceScriptStatus: async id => local(id),
    getDeviceScriptCatalog: async () => ({ protocol_version: 1, scripts: [item] }),
    getDeviceScriptPolicy: async () => { throw new APIError(404); },
    getDeviceReadPolicy: async () => { throw new APIError(404); },
    getDeviceScreenPolicy: async () => { throw new APIError(404); },
    publishDeviceScriptPolicy: async (id, body) => { calls.push(body); return { device_id: id, project_id: 'project', status: 'active', verified: false, policy: { ...body, revision: 1 } }; },
    revokeDeviceScriptPolicy: async () => { throw new Error('not expected'); },
    ...overrides,
  };
  const utils = load('utils/deviceScriptPolicy.ts', {});
  const { useDeviceScriptStore: store } = load('stores/deviceScriptStore.ts', {
    './authStore': { useAuthStore: { getState: () => ({ user }), subscribe: listener => { listeners.push(listener); return () => {}; } } },
    '@/services/api': { APIError }, '@/services/deviceControlApi': api,
    '@/utils/deviceScriptPolicy': utils,
  });
  const switchUser = next => { user = next; listeners.forEach(listener => listener({ user })); };
  return { store, calls, switchUser };
}

test('an ordinary employee cannot start management requests', () => {
  const { store, switchUser } = fixture();
  switchUser({ id: 'staff', project_id: 'project', role: 'agent' });
  assert.equal(store.getState().open(device('a')), null);
  assert.equal(store.getState().owner, null);
});

test('late data from a closed device cannot replace the new device scope', async () => {
  const pending = deferred();
  const { store } = fixture({ getDeviceScriptStatus: id => id === 'a' ? pending.promise : Promise.resolve(local(id)) });
  const first = store.getState().open(device('a'));
  store.getState().close(first);
  store.getState().open(device('b'));
  await tick();
  pending.resolve(local('a'));
  await tick();
  assert.equal(store.getState().owner.deviceId, 'b');
  assert.equal(store.getState().local.device_id, 'b');
});

test('switching accounts clears metadata and ignores an in-flight publication', async () => {
  const pending = deferred();
  let count = 0;
  const { store, switchUser } = fixture({ publishDeviceScriptPolicy: async () => { count++; return pending.promise; } });
  store.getState().open(device('a'));
  await tick();
  const operation = store.getState().publish(form);
  await tick();
  switchUser({ id: 'other', project_id: 'project', role: 'admin' });
  assert.equal(store.getState().owner, null);
  assert.equal(store.getState().catalog, null);
  pending.resolve({ device_id: 'a', project_id: 'project', status: 'active', verified: false, policy: { revision: 1 } });
  await operation;
  assert.equal(store.getState().policy, null);
  assert.equal(count, 1);
});

test('an active other mode prevents publication and repeated clicks submit once', async () => {
  const { store, calls } = fixture({ getDeviceReadPolicy: async () => ({ device_id: 'a', project_id: 'project', status: 'active' }) });
  store.getState().open(device('a'));
  await tick();
  await store.getState().publish(form);
  assert.equal(calls.length, 0);
  const pending = deferred();
  let count = 0;
  const second = fixture({ publishDeviceScriptPolicy: async () => { count++; return pending.promise; } });
  second.store.getState().open(device('b'));
  await tick();
  const operation = second.store.getState().publish(form);
  await second.store.getState().publish(form);
  assert.equal(count, 1);
  pending.resolve({ device_id: 'b', project_id: 'project', status: 'active', verified: false, policy: { ...item, revision: 1, max_runs: 1, timeout_seconds: 10 } });
  await operation;
});
