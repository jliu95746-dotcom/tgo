import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function harness() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const listeners = [], calls = [], pending = [];
  const auth = { getState: () => ({ user }), subscribe: callback => listeners.push(callback) };
  const create = initializer => {
    let state;
    const set = next => { state = { ...state, ...(typeof next === 'function' ? next(state) : next) }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  class APIError extends Error { constructor(status) { super('synthetic error'); this.status = status; } }
  const api = new Proxy({}, { get: (_, name) => (...args) => {
    calls.push({ name, args });
    return new Promise((resolve, reject) => pending.push({ name, resolve, reject }));
  } });
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/agentTrialStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  let key = 0;
  vm.runInNewContext(source, { exports, AbortController, crypto: { randomUUID: () => `request-${++key}` }, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: auth };
    if (name === '@/services/agentTrialsApi') return { AgentTrialsApi: api };
    if (name === '@/services/api') return { APIError };
    throw new Error(`Unexpected import: ${name}`);
  } });
  const take = name => { const i = pending.findIndex(value => value.name === name); assert.notEqual(i, -1); return pending.splice(i, 1)[0]; };
  return {
    store: exports.useAgentTrialStore, calls,
    resolve: (name, value) => take(name).resolve(value),
    reject: (name, status = 0) => take(name).reject(new APIError(status)),
    changeUser: next => { const previous = { user }; user = next; listeners.forEach(fn => fn({ user }, previous)); },
  };
}

const report = (changes = {}) => ({ id: 'report-a', agent_id: 'agent-a', request_key: 'request-1',
  status: 'completed', assessment: 'not_reviewed', can_enable: false, mode: 'knowledge_grounded',
  channel: 'web', question: '材质？', answer: '依据资料回答', sources: [], created_at: '2026-10-11T00:00:00Z', ...changes });
async function select(fixture, agent = 'agent-a') {
  const loading = fixture.store.getState().select(agent);
  fixture.resolve('list', []);
  await loading;
}
const tick = () => new Promise(resolve => setImmediate(resolve));

const applicability = (status, time = '2026-10-11T20:00:00Z') => ({
  scope: 'agent_configuration_and_knowledge', status, checked_at: time, issues: [],
});

test('refresh updates applicability while retaining historical answer and terminal state', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('材质？');
  f.resolve('start', report({ answer: '历史回答', applicability: applicability('current') })); await sending;
  const refreshing = f.store.getState().refresh();
  f.resolve('list', [report({ answer: '不应替换', applicability: applicability('changed', '2026-10-11T20:01:00Z') })]);
  await refreshing;
  assert.equal(f.store.getState().report.applicability.status, 'changed');
  assert.equal(f.store.getState().report.answer, '历史回答');
  assert.equal(f.store.getState().report.can_enable, false);
});

test('an older check cannot replace a newer requirement to retest', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('材质？');
  f.resolve('start', report({ applicability: applicability('changed', '2026-10-11T20:01:00Z') })); await sending;
  const refreshing = f.store.getState().refresh();
  f.resolve('list', [report({ applicability: applicability('current') })]); await refreshing;
  assert.equal(f.store.getState().report.applicability.status, 'changed');
});

test('an old server without applicability cannot keep a prior current claim', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('材质？');
  f.resolve('start', report({ applicability: applicability('current') })); await sending;
  const refreshing = f.store.getState().refresh();
  f.resolve('list', [report()]); await refreshing;
  assert.equal(f.store.getState().report.applicability.status, 'not_recorded');
});

test('a double click submits once and completed reports never grant activation', async () => {
  const f = harness(); await select(f);
  const first = f.store.getState().start('材质？');
  await f.store.getState().start('另一个问题');
  assert.equal(f.calls.filter(value => value.name === 'start').length, 1);
  f.resolve('start', report()); await first;
  assert.equal(f.store.getState().report.can_enable, false);
  assert.equal(f.store.getState().pending, null);
});

test('an unknown result preserves the exact request and prevents a new question', async () => {
  const f = harness(); await select(f);
  const first = f.store.getState().start('材质？');
  f.reject('start'); await first;
  await f.store.getState().start('修改后的问题');
  const retry = f.store.getState().retry();
  const requests = f.calls.filter(value => value.name === 'start');
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0].args[1], requests[1].args[1]);
  f.resolve('start', report()); await retry;
});

test('identity changes abort requests, clear questions and ignore late results', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('私有测试内容');
  const signal = f.calls.find(value => value.name === 'start').args[2];
  f.changeUser({ id: 'staff-b', project_id: 'project-b', role: 'admin' });
  assert.equal(signal.aborted, true);
  f.resolve('start', report()); await sending;
  assert.equal(f.store.getState().report, null);
  assert.equal(f.store.getState().pending, null);
  assert.equal(Object.keys(f.store.getState().sessions).length, 0);
});

test('changing employee ignores the previous response and reopening recovers the original request', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('材质？');
  await select(f, 'agent-b');
  f.resolve('start', report()); await sending;
  assert.equal(f.store.getState().report, null);
  const reopening = f.store.getState().select('agent-a');
  f.resolve('list', []); await tick();
  f.resolve('byRequest', report()); await reopening;
  assert.equal(f.store.getState().report.id, 'report-a');
  assert.equal(f.calls.filter(value => value.name === 'start').length, 1);
});

test('cancelling an in-flight trial retrieves its report and ignores a late running response', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('材质？');
  const cancelling = f.store.getState().cancel();
  f.resolve('byRequest', report({ status: 'running' })); await tick();
  f.resolve('cancel', report({ status: 'cancelled', answer: '' })); await cancelling;
  f.resolve('start', report({ status: 'running', answer: '' })); await sending;
  assert.equal(f.store.getState().report.status, 'cancelled');
  assert.equal(f.store.getState().pending, null);
});

test('foreign employee or mismatched request reports are rejected', async () => {
  for (const changes of [{ agent_id: 'agent-b' }, { request_key: 'other-request' }, { can_enable: true }]) {
    const f = harness(); await select(f);
    const sending = f.store.getState().start('材质？');
    f.resolve('start', report(changes)); await sending;
    assert.equal(f.store.getState().report, null);
    assert.equal(f.store.getState().error, 'invalidReport');
  }
});

test('running history blocks a fresh generation and can be cancelled after reload', async () => {
  const f = harness();
  const loading = f.store.getState().select('agent-a');
  f.resolve('list', [report({ status: 'running' })]); await loading;
  await f.store.getState().start('另一个问题');
  assert.equal(f.calls.filter(value => value.name === 'start').length, 0);
  const cancelling = f.store.getState().cancel();
  f.resolve('cancel', report({ status: 'cancelled', answer: '' })); await cancelling;
  assert.equal(f.store.getState().pending, null);
});

test('downgrading administrator role clears trial state and makes no further call', async () => {
  const f = harness(); await select(f);
  f.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  await f.store.getState().select('agent-a');
  await f.store.getState().start('材质？');
  assert.equal(f.calls.length, 1);
  assert.equal(f.store.getState().agentId, null);
});

test('a failed history read cannot silently become an empty history and allow generation', async () => {
  const f = harness();
  const loading = f.store.getState().select('agent-a');
  f.reject('list', 503); await loading;
  await f.store.getState().start('材质？');
  assert.equal(f.calls.filter(value => value.name === 'start').length, 0);
  assert.equal(f.store.getState().error, 'unavailable');
});

test('late cleanup of an aborted request cannot unlock a new retry', async () => {
  const f = harness(); await select(f);
  const original = f.store.getState().start('材质？');
  const cancelling = f.store.getState().cancel();
  f.reject('byRequest', 404); await cancelling;
  const retry = f.store.getState().retry();
  f.resolve('start', report({ status: 'running' })); await original;
  assert.equal(f.store.getState().submitting, true);
  await f.store.getState().retry();
  assert.equal(f.calls.filter(value => value.name === 'start').length, 2);
  f.resolve('start', report()); await retry;
});

test('a stale running history response cannot overwrite a cancelled report', async () => {
  const f = harness(); await select(f);
  const sending = f.store.getState().start('材质？');
  const refreshing = f.store.getState().refresh();
  const cancelling = f.store.getState().cancel();
  f.resolve('byRequest', report({ status: 'running' })); await tick();
  f.resolve('cancel', report({ status: 'cancelled', answer: '' })); await cancelling;
  f.resolve('list', [report({ status: 'running' })]); await refreshing;
  f.resolve('start', report({ status: 'running' })); await sending;
  assert.equal(f.store.getState().report.status, 'cancelled');
  assert.equal(f.store.getState().pending, null);
});
