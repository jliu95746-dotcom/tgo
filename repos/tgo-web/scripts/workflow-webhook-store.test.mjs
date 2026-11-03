import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const copy = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const sourceState = () => ({ webhook: null, binding_current: false, registration_available: true,
  execution_available: true, current_release: { release_id: 'release-a', definition_digest: 'a'.repeat(64), version: 1 }, reason: null });
const summary = request => ({ id: 'hook-a', project_id: 'project-a', workflow_id: 'flow-a',
  revision: request.expected_revision + 1, credential_revision: 1, key_id: 'key-a', enabled: request.enabled ?? false,
  release_id: request.expected_release_id ?? 'release-a', release_digest: request.expected_release_digest ?? 'a'.repeat(64) });
const receipt = (request, operation = 'save') => ({ request_id: request.request_id, operation,
  webhook: summary(request), replayed: false, credential_status: 'available', credential: { secret: 'synthetic-sensitive-credential' } });
function fixture(overrides = {}) {
  let owner = 'project-a:staff-a:admin';
  let count = 0;
  const exports = {}, storage = new Map(), calls = [], remote = sourceState();
  const api = { getState: async () => remote, getHistory: async (_id, offset) => ({ project_id: 'project-a', workflow_id: 'flow-a', items: [], total: 0, limit: 10, offset, has_more: false }),
    save: async (id, request) => { calls.push({ id, request: copy(request), operation: 'save' }); remote.webhook = summary(request); return receipt(request); },
    disable: async (id, request) => receipt(request, 'disable'), rotate: async (id, request) => receipt(request, 'rotate'),
    getCommand: async () => { throw Error('not found'); }, ...overrides };
  const source = readFileSync(new URL('../src/stores/workflowWebhookActions.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports, AbortController, Error, Promise, JSON });
  const state = exports.newWebhookState();
  const actions = exports.createWebhookActions(value => Object.assign(state, value), () => state, {
    api, currentOwner: () => owner, createId: () => `00000000-0000-4000-8000-${String(++count).padStart(12, '0')}`,
    translate: key => key, storage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  });
  return { state, actions, api, storage, calls, setOwner: value => { owner = value; }, activate: () => actions.activate(owner, 'flow-a') };
}

test('save binds verified current release and never stores returned credential', async () => {
  const h = fixture(); h.activate(); await flush(); await h.actions.save(false);
  assert.equal(h.calls[0].request.expected_release_id, 'release-a');
  assert.equal(h.calls[0].request.expected_revision, 0);
  assert.equal(h.state.credential.secret, 'synthetic-sensitive-credential');
  assert.equal([...h.storage.values()].join('').includes('synthetic-sensitive'), false);
  h.actions.hideCredential(); assert.equal(h.state.credential, null);
});
test('capability and missing published release prevent enabling', async () => {
  for (const patch of [{ execution_available: false }, { registration_available: false }, { current_release: null }]) {
    const h = fixture({ getState: async () => ({ ...sourceState(), ...patch }) });
    h.activate(); await flush(); await h.actions.save(true); assert.equal(h.calls.length, 0);
  }
});
test('unknown network result keeps exact original request for explicit retry', async () => {
  let seen;
  const h = fixture({ save: async (_id, request) => { seen = copy(request); throw Error('lost acknowledgement'); } });
  h.activate(); await flush(); await h.actions.save(false);
  assert.ok(h.state.pending); assert.equal(h.storage.size, 1);
  h.api.save = async (_id, request) => { assert.deepEqual(copy(request), seen); return receipt(request); };
  await h.actions.retry(); assert.equal(h.state.pending, null); assert.equal(h.storage.size, 0);
});
test('stale command receipt cannot become current config or disclose stale credential', async () => {
  const h = fixture({ save: async (_id, request) => ({ ...receipt(request), request_id: 'wrong-request' }) });
  h.activate(); await flush(); await h.actions.save(false);
  assert.ok(h.state.pending); assert.equal(h.state.credential, null);
});
test('account change drops late command reply even before effect cleanup', async () => {
  const waiting = deferred(); const h = fixture({ save: async () => waiting.promise });
  h.activate(); await flush(); const result = h.actions.save(false);
  h.setOwner('project-b:staff-b:admin'); waiting.resolve(receipt({ request_id: 'wrong-request', expected_revision: 0 }));
  await result; assert.equal(h.state.credential, null); assert.equal(h.state.notice, '');
});
test('unmount clears credentials and aborts late reads', async () => {
  const waiting = deferred(); const h = fixture({ getState: async () => waiting.promise });
  const cleanup = h.activate(); cleanup(); waiting.resolve(sourceState()); await flush();
  assert.equal(h.state.state, null); assert.equal(h.state.ownerKey, null);
});
test('malformed stored command fails closed without posting', async () => {
  const h = fixture(); h.storage.set('workflow-webhook-command:project-a:staff-a:admin:flow-a', '{"credential":"forbidden"}');
  h.activate(); await flush(); await h.actions.save(false); assert.equal(h.calls.length, 0);
  assert.match(h.state.error, /storageFailed/);
});
test('history from a foreign workflow is rejected', async () => {
  const h = fixture({ getHistory: async () => ({ project_id: 'project-a', workflow_id: 'other', items: [], total: 0, limit: 10, offset: 0, has_more: false }) });
  h.activate(); await flush(); assert.equal(h.state.history, null); assert.match(h.state.historyError, /invalidResponse/);
});
test('older recovered credentials stay hidden after a newer rotation', async () => {
  const h = fixture({ getState: async () => ({ ...sourceState(), webhook: { ...summary({ expected_revision: 3 }), credential_revision: 2, key_id: 'newer' } }) });
  h.activate(); await flush(); await h.actions.save(false); assert.equal(h.state.credential, null);
});
test('stored request survives reactivation and explicit recovery uses its original ID', async () => {
  let original;
  const h = fixture({ save: async (_id, request) => { original = copy(request); throw Error('lost'); },
    getCommand: async (_id, requestId) => { assert.equal(requestId, original.request_id); return receipt(original); } });
  const cleanup = h.activate(); await flush(); await h.actions.save(false); cleanup();
  h.activate(); await flush(); assert.equal(h.state.pending.request.request_id, original.request_id);
  await h.actions.recover(); assert.equal(h.state.pending, null); assert.equal(h.storage.size, 0);
});
