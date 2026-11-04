import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const context = { workflow_id: 'flow-a', event_type: 'conversation.archived', release_id: 'release-a', release_digest: 'digest-a', version: 3, status: 'active' };
const run = { id: 'run-a', workflow_id: 'flow-a', status: 'pending', node_executions: [], started_at: '2026-10-23T10:00:00Z' };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture(overrides = {}) {
  let owner = 'project-a:staff-a:admin';
  const calls = [];
  const timers = new Map();
  let nextTimer = 0;
  let ids = 0;
  const api = {
    getEventTestContext: async (id, signal) => { calls.push({ method: 'context', id, signal }); return { ...context, workflow_id: id }; },
    sendEventTest: async (id, payload, signal) => { calls.push({ method: 'send', id, payload, signal }); return { execution_id: 'run-a', status: 'pending', deduplicated: false }; },
    getExecution: async (id, signal) => { calls.push({ method: 'get', id, signal }); return { ...run }; },
    cancelExecution: async (id, signal) => { calls.push({ method: 'cancel', id, signal }); },
    ...overrides,
  };
  const state = { ownerKey: null, activeWorkflowId: null, sessions: {} };
  const exports = {};
  const source = readFileSync(new URL('../src/stores/workflowEventTestActions.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  } }).outputText, {
    exports, AbortController, TextEncoder, JSON, Error, Object, Number,
    setTimeout: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
  });
  const set = update => Object.assign(state, typeof update === 'function' ? update(state) : update);
  const actions = exports.createWorkflowEventTestActions(set, () => state, {
    api, currentOwner: () => owner, createId: () => `request-${++ids}`, translate: key => key,
  });
  const current = () => state.sessions['flow-a'];
  const activate = () => actions.activate(owner, 'flow-a');
  const tick = async () => { const entry = timers.entries().next().value; assert.ok(entry); timers.delete(entry[0]); await entry[1](); await flush(); };
  return { state, actions, api, calls, timers, current, activate, tick, setOwner: value => { owner = value; }, getIds: () => ids };
}

test('sending binds the published context and locks event data for the same request', async () => {
  const h = fixture(); h.activate(); await flush();
  h.actions.setInput('{"question":"需要整理","count":2}');
  await h.actions.send();
  const sent = h.calls.find(call => call.method === 'send');
  assert.equal(sent.payload.event_type, context.event_type);
  assert.equal(sent.payload.expected_release_id, context.release_id);
  assert.equal(sent.payload.expected_release_digest, context.release_digest);
  assert.equal(sent.payload.data.question, '需要整理');
  h.actions.setInput('{"question":"已改变"}');
  assert.equal(h.current().attempt.data.question, '需要整理');
  assert.equal(h.current().jsonInput, '{"question":"需要整理","count":2}');
  assert.equal(h.current().execution.id, 'run-a');
});

test('an uncertain POST is retried with exactly the same request and survives closing the panel', async () => {
  const h = fixture(); const close = h.activate(); await flush();
  const sent = [];
  h.api.sendEventTest = async (_id, payload) => { sent.push(payload); if (sent.length === 1) throw Error('network lost'); return { execution_id: 'run-a', status: 'pending', deduplicated: true }; };
  h.actions.setInput('{"text":"原始内容"}'); await h.actions.send();
  h.actions.reset();
  assert.equal(h.current().attempt.request_id, 'request-1');
  close(); h.activate(); await flush(); await h.actions.send();
  assert.equal(h.getIds(), 1);
  assert.deepEqual(sent[1], sent[0]);
  assert.equal(h.current().accepted.deduplicated, true);
});

test('context responses from a previous workflow cannot update the active workflow', async () => {
  const pending = deferred();
  const h = fixture({ getEventTestContext: id => id === 'flow-a' ? pending.promise : Promise.resolve({ ...context, workflow_id: id }) });
  h.activate(); h.actions.activate('project-a:staff-a:admin', 'flow-b'); await flush();
  pending.resolve(context); await flush();
  assert.equal(h.state.activeWorkflowId, 'flow-b');
  assert.equal(h.current().context, null);
  assert.equal(h.state.sessions['flow-b'].context.workflow_id, 'flow-b');
});

test('identity changes ignore outstanding requests even before the effect cleanup runs', async () => {
  const pending = deferred(); const h = fixture({ getEventTestContext: () => pending.promise });
  h.activate(); h.setOwner('project-b:staff-b:admin'); pending.resolve(context); await flush();
  assert.equal(h.current().context, null);
  await h.actions.send(); assert.equal(h.calls.filter(call => call.method === 'send').length, 0);
  h.actions.activate('project-b:staff-b:admin', 'flow-b'); await flush();
  assert.equal(h.state.sessions['flow-a'], undefined);
});

test('closing aborts requests and stops polling; reopening observes the existing run', async () => {
  const h = fixture(); const close = h.activate(); await flush(); await h.actions.send();
  assert.equal(h.timers.size, 1);
  const request = h.calls.find(call => call.method === 'get'); close();
  assert.equal(request.signal.aborted, true); assert.equal(h.timers.size, 0);
  h.activate(); await flush();
  assert.equal(h.calls.filter(call => call.method === 'send').length, 1);
  assert.equal(h.calls.filter(call => call.method === 'get').length, 2);
});

test('polling shows the persisted terminal output and stops scheduling', async () => {
  const h = fixture(); h.activate(); await flush(); await h.actions.send();
  h.api.getExecution = async () => ({ ...run, status: 'completed', output: '已整理' });
  await h.tick();
  assert.equal(h.current().execution.output, '已整理'); assert.equal(h.timers.size, 0);
  h.actions.reset(); await flush();
  assert.equal(h.current().attempt, null);
  await h.actions.send(); assert.equal(h.getIds(), 2);
});

test('cancelling reads the actual record when completion wins the cancellation race', async () => {
  const h = fixture(); h.activate(); await flush(); await h.actions.send();
  h.api.cancelExecution = async () => { throw Error('already finished'); };
  h.api.getExecution = async () => ({ ...run, status: 'completed', output: '已完成' });
  await h.actions.cancel();
  assert.equal(h.current().execution.status, 'completed');
  assert.equal(h.current().error, ''); assert.equal(h.timers.size, 0);
});

test('invalid JSON objects and non-finite values are rejected before creating a request', async () => {
  const h = fixture(); h.activate(); await flush();
  for (const input of ['[1]', 'null', '{', '{"value":1e999}']) {
    h.actions.setInput(input); await h.actions.send();
    assert.equal(h.current().error, 'workflowEvents.invalidJson');
  }
  h.actions.setInput(JSON.stringify({ text: '文'.repeat(21000) })); await h.actions.send();
  assert.equal(h.current().error, 'workflowEvents.tooLarge');
  assert.equal(h.getIds(), 0);
});

test('a definitive publication rejection permits reset and context reload', async () => {
  const h = fixture(); h.activate(); await flush();
  h.api.sendEventTest = async () => { throw Object.assign(Error('release changed'), { status: 409 }); };
  await h.actions.send(); assert.equal(h.current().rejected, true);
  h.api.getEventTestContext = async () => ({ ...context, release_id: 'release-b', version: 4 });
  h.actions.reset(); await flush();
  assert.equal(h.current().context.release_id, 'release-b');
  assert.equal(h.current().attempt, null);
});

test('a late POST after unmount cannot start polling or change the retained attempt', async () => {
  const pending = deferred(); const h = fixture({ sendEventTest: () => pending.promise });
  const close = h.activate(); await flush(); const sending = h.actions.send(); close();
  pending.resolve({ execution_id: 'run-a', status: 'pending', deduplicated: false }); await sending;
  assert.equal(h.current().accepted, null); assert.equal(h.current().attempt.request_id, 'request-1');
  assert.equal(h.timers.size, 0); assert.equal(h.calls.filter(call => call.method === 'get').length, 0);
});

test('a repeated click while submission is pending cannot create another event', async () => {
  const pending = deferred(); let sends = 0;
  const h = fixture({ sendEventTest: () => { sends += 1; return pending.promise; } });
  h.activate(); await flush(); const sending = h.actions.send(); await h.actions.send();
  assert.equal(sends, 1); assert.equal(h.getIds(), 1);
  pending.resolve({ execution_id: 'run-a', status: 'pending', deduplicated: false }); await sending;
});

test('an unsuccessful cancellation remains visible while the run continues', async () => {
  const h = fixture(); h.activate(); await flush(); await h.actions.send();
  h.api.cancelExecution = async () => { throw Error('cancel service unavailable'); };
  await h.actions.cancel(); await h.tick();
  assert.equal(h.current().execution.status, 'pending');
  assert.equal(h.current().error, 'cancel service unavailable');
});

test('an unrelated execution response is rejected without exposing its data', async () => {
  const h = fixture({ getExecution: async () => ({ ...run, workflow_id: 'another-flow', output: 'other workflow data' }) });
  h.activate(); await flush(); await h.actions.send();
  assert.equal(h.current().execution, null);
  assert.equal(h.current().error, 'workflowEvents.invalidResult');
  assert.equal(h.timers.size, 0);
});
