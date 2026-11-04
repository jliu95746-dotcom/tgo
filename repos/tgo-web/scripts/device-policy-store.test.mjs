import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const load = (path, dependencies) => {
  const exports = {};
  const source = readFileSync(new URL('../src/' + path, import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText,
    { exports, AbortController, Number, RegExp, require: name => dependencies[name] ?? require(name) });
  return exports;
};
class APIError extends Error { constructor(status) { super('fixture'); this.status = status; } }
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const device = id => ({ id, project_id: 'project', device_name: 'synthetic', status: 'online' });
const local = id => ({ device_id: id, project_id: 'project', connection_instance_id: 'c'.repeat(32),
  grant: { protocol_version: 1, active: true, grant_id: 'a'.repeat(32), max_actions: 10, used_actions: 0,
    width: 640, height: 480, dpi: 120, allow_click: false, allow_text: false, click_regions: [] } });
const policy = (id, revision = 1) => ({ device_id: id, project_id: 'project', status: 'pending', verified: false,
  policy: { protocol_version: 1, revision, grant_id: 'a'.repeat(32), max_actions: 5, allow_click: false, allow_text: false, click_regions: [], digest: 'd'.repeat(64) } });
const form = { maxActions: 5, allowClick: false, allowText: false, regions: [] };
function setup(changes = {}) {
  const auth = { user: { id: 'admin', project_id: 'project', role: 'admin' } };
  const published = []; const revoked = [];
  const api = {
    getDeviceReadPolicy: async () => { throw new APIError(404); },
    getDeviceScreenPolicy: async () => { throw new APIError(404); },
    getDeviceScreenGrant: async id => local(id),
    publishDeviceScreenPolicy: async (id, payload) => { published.push(payload); return policy(id, payload.expected_policy_revision + 1); },
    revokeDeviceScreenPolicy: async (id, revision) => { revoked.push(revision); return { ...policy(id, revision + 1), status: 'revoked' }; },
    ...changes,
  };
  const helpers = load('utils/deviceScreenPolicy.ts', {});
  const store = load('stores/devicePolicyStore.ts', {
    './authStore': { useAuthStore: { getState: () => auth } },
    '@/services/api': { APIError }, '@/services/deviceControlApi': api,
    '@/utils/deviceScreenPolicy': helpers,
  }).useDevicePolicyStore;
  return { store, auth, published, revoked };
}
test('late response from a closed device cannot overwrite a new selection', async () => {
  const pending = deferred();
  const { store } = setup({ getDeviceScreenGrant: id => id === 'old' ? pending.promise : Promise.resolve(local(id)) });
  const old = store.getState().open(device('old'));
  store.getState().close(old);
  store.getState().open(device('new'));
  await tick();
  pending.resolve(local('old')); await tick();
  assert.equal(store.getState().local.device_id, 'new');
});
test('an account change makes an in-flight response and subsequent actions unusable', async () => {
  const pending = deferred();
  const { store, auth, published } = setup({ getDeviceScreenGrant: () => pending.promise });
  store.getState().open(device('own'));
  auth.user = { ...auth.user, id: 'another-user' };
  pending.resolve(local('own')); await tick();
  await store.getState().publish(form);
  assert.equal(store.getState().loaded, false);
  assert.equal(store.getState().local, null);
  assert.equal(published.length, 0);
});
test('failed reload preserves known data but blocks mutation until a successful retry', async () => {
  let fail = false;
  const { store, published } = setup({ getDeviceScreenGrant: async id => { if (fail) throw new Error('offline'); return local(id); } });
  store.getState().open(device('own')); await tick();
  fail = true; await store.getState().refresh();
  assert.equal(store.getState().error, true);
  await store.getState().publish(form);
  assert.equal(published.length, 0);
  fail = false; await store.getState().refresh();
  await store.getState().publish(form);
  assert.equal(published.length, 1);
  assert.equal(published[0].allow_click, false);
});
test('a superseded refresh cannot replace the latest grant status', async () => {
  const pending = deferred(); let calls = 0;
  const { store } = setup({ getDeviceScreenGrant: id => ++calls === 1 ? pending.promise : Promise.resolve(local(id)) });
  store.getState().open(device('own'));
  await store.getState().refresh();
  pending.resolve({ ...local('own'), grant: { ...local('own').grant, active: false } }); await tick();
  assert.equal(store.getState().local.grant.active, true);
});
test('a stale confirmation version does not revoke a newer policy', async () => {
  const { store, revoked } = setup({ getDeviceScreenPolicy: async id => policy(id, 4) });
  store.getState().open(device('own')); await tick();
  await store.getState().revoke({ kind: 'screen', revision: 3 });
  assert.equal(revoked.length, 0);
  await store.getState().revoke({ kind: 'screen', revision: 4 });
  assert.deepEqual(revoked, [4]);
});
test('foreign policy responses cannot become a usable authorization', async () => {
  const { store, published } = setup({ getDeviceScreenPolicy: async () => policy('foreign') });
  store.getState().open(device('own')); await tick();
  assert.equal(store.getState().error, true);
  await store.getState().publish(form);
  assert.equal(published.length, 0);
});
