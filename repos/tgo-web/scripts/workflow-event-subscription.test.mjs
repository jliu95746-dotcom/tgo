import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const context = { workflow_id: 'flow-a', event_type: 'staff.session.closed', release_id: 'release-a', release_digest: 'digest-a', version: 3, status: 'active' };
const subscription = { workflow_id: 'flow-a', subscription_id: null, event_type: 'staff.session.closed', enabled: false, revision: 0, release_id: null, release_digest: null, version: null, enabled_at: null };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

test('service closes subscriptions with DELETE so paused companies can stop automation', async () => {
  const exports = {}, calls = [];
  const api = {
    delete: async (...args) => { calls.push(['delete', ...args]); return subscription; },
    put: async (...args) => { calls.push(['put', ...args]); return subscription; },
  };
  const source = readFileSync(new URL('../src/services/workflowApi.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, { exports, require: name => {
    if (name === './api') return api;
    if (name === '@/i18n') return { t: key => key };
    throw Error(`Unexpected dependency ${name}`);
  } });
  const signal = new AbortController().signal;
  await exports.WorkflowApiService.updateEventSubscription('flow-a', { enabled: false, expected_revision: 7 }, signal);
  assert.equal(calls[0][0], 'delete');
  assert.equal(calls[0][1], '/v1/ai/workflows/flow-a/event-subscription?expected_revision=7');
  assert.equal(calls[0][2].signal, signal);
  await exports.WorkflowApiService.updateEventSubscription('flow-a', { enabled: true, expected_revision: 8 }, signal);
  assert.equal(calls[1][0], 'put');
});

function fixture(overrides = {}) {
  let owner = 'project-a:staff-a:admin';
  const calls = [], exports = {};
  const api = {
    getEventSubscription: async (id, signal) => { calls.push({ method: 'get', id, signal }); return { ...subscription, workflow_id: id }; },
    getEventTestContext: async id => ({ ...context, workflow_id: id }),
    listEventDeliveries: async () => ({ items: [] }),
    updateEventSubscription: async (id, body, signal) => { calls.push({ method: 'put', id, body, signal }); return { ...subscription, workflow_id: id, enabled: body.enabled, revision: body.expected_revision + 1, release_id: body.expected_release_id ?? null, release_digest: body.expected_release_digest ?? null }; },
    ...overrides,
  };
  const source = readFileSync(new URL('../src/stores/workflowEventSubscriptionActions.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports, AbortController, Error, Promise });
  const state = exports.newEventSubscriptionState();
  const set = update => Object.assign(state, typeof update === 'function' ? update(state) : update);
  const actions = exports.createWorkflowEventSubscriptionActions(set, () => state, { api, currentOwner: () => owner, translate: key => key });
  return { state, actions, calls, api, activate: () => actions.activate(owner, 'flow-a'), setOwner: value => { owner = value; } };
}

test('enabling fixes the current published binding and optimistic subscription revision', async () => {
  const h = fixture(); h.activate(); await flush(); await h.actions.setEnabled(true);
  const sent = h.calls.find(call => call.method === 'put');
  assert.deepEqual(JSON.parse(JSON.stringify(sent.body)), { enabled: true, expected_revision: 0, expected_release_id: 'release-a', expected_release_digest: 'digest-a' });
  assert.equal(h.state.subscription.enabled, true);
});

test('context failures do not prevent closing an enabled subscription', async () => {
  const h = fixture({ getEventTestContext: async () => { throw Error('Workflow disabled'); }, getEventSubscription: async () => ({ ...subscription, enabled: true, revision: 7 }) });
  h.activate(); await flush(); await h.actions.setEnabled(false);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.find(call => call.method === 'put').body)), { enabled: false, expected_revision: 7 });
  assert.match(h.state.contextError, /Workflow disabled/);
});

test('arbitrary manual event names cannot enable a real subscription', async () => {
  const h = fixture({ getEventTestContext: async () => ({ ...context, event_type: 'other.event' }) });
  h.activate(); await flush(); await h.actions.setEnabled(true);
  assert.equal(h.calls.filter(call => call.method === 'put').length, 0);
});

test('re-enabling a stale subscription binds the new release, never its old one', async () => {
  const h = fixture({ getEventSubscription: async () => ({ ...subscription, enabled: true, revision: 4, release_id: 'older', release_digest: 'older-digest' }) });
  h.activate(); await flush(); await h.actions.setEnabled(true);
  assert.equal(h.calls.find(call => call.method === 'put').body.expected_release_id, 'release-a');
  assert.equal(h.calls.find(call => call.method === 'put').body.expected_revision, 4);
});

test('account changes reject reads and mutations before effect cleanup', async () => {
  const pending = deferred(); const h = fixture({ getEventSubscription: () => pending.promise });
  h.activate(); h.setOwner('project-b:staff-a:admin'); pending.resolve(subscription); await flush();
  assert.equal(h.state.subscription, null); await h.actions.setEnabled(true);
  assert.equal(h.calls.filter(call => call.method === 'put').length, 0);
});

test('switching workflow or closing aborts reads and ignores late responses', async () => {
  const pending = deferred(); const h = fixture({ getEventSubscription: id => id === 'flow-a' ? pending.promise : Promise.resolve({ ...subscription, workflow_id: id }) });
  const closeOld = h.activate(); const closeNew = h.actions.activate('project-a:staff-a:admin', 'flow-b');
  closeOld(); pending.resolve(subscription); await flush();
  assert.equal(h.state.workflowId, 'flow-b'); assert.equal(h.state.subscription.workflow_id, 'flow-b');
  closeNew(); assert.equal(h.state.subscription, null);
});

test('a mutation response from the prior workflow cannot replace current state', async () => {
  const pending = deferred(); const h = fixture({ updateEventSubscription: () => pending.promise });
  h.activate(); await flush(); const sending = h.actions.setEnabled(true);
  h.actions.activate('project-a:staff-a:admin', 'flow-b'); await flush();
  pending.resolve({ ...subscription, enabled: true }); await sending;
  assert.equal(h.state.subscription.workflow_id, 'flow-b'); assert.equal(h.state.subscription.enabled, false);
});

test('uncertain writes reread the server before permitting a second mutation', async () => {
  const pending = deferred(); const h = fixture(); h.activate(); await flush();
  h.api.updateEventSubscription = async () => { throw Error('Response lost'); };
  h.api.getEventSubscription = () => pending.promise;
  const saving = h.actions.setEnabled(true); await flush();
  assert.equal(h.state.saving, true); await h.actions.setEnabled(false);
  pending.resolve({ ...subscription, enabled: true, revision: 8 }); await saving;
  assert.equal(h.state.subscription.revision, 8); assert.equal(h.state.saving, false);
  assert.match(h.state.error, /Response lost/);
});

test('delivery failures do not hide the subscription or its close action', async () => {
  const h = fixture({ listEventDeliveries: async () => { throw Error('Delivery history unavailable'); } });
  h.activate(); await flush(); assert.ok(h.state.subscription); assert.match(h.state.deliveryError, /Delivery history unavailable/);
});

test('a mismatched workflow response is rejected instead of exposing its state', async () => {
  const h = fixture({ getEventSubscription: async () => ({ ...subscription, workflow_id: 'flow-b' }) });
  h.activate(); await flush(); assert.equal(h.state.subscription, null); assert.ok(h.state.error);
});
