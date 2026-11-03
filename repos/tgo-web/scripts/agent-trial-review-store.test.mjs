import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function harness() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const calls = [], requests = [], listeners = [];
  const create = initializer => {
    let state;
    const set = next => { state = { ...state, ...(typeof next === 'function' ? next(state) : next) }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  class APIError extends Error { constructor(status) { super('synthetic'); this.status = status; } }
  const api = new Proxy({}, { get: (_, name) => (...args) => {
    calls.push({ name, args });
    return new Promise((resolve, reject) => requests.push({ name, resolve, reject }));
  } });
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/agentTrialReviewStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  let sequence = 0;
  vm.runInNewContext(source, { exports, AbortController, crypto: { randomUUID: () => `key-${++sequence}` }, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: {
      getState: () => ({ user }), subscribe: callback => listeners.push(callback),
    } };
    if (name === '@/services/agentTrialReviewsApi') return { AgentTrialReviewsApi: api };
    if (name === '@/services/api') return { APIError };
    throw new Error(`Unexpected import ${name}`);
  } });
  const take = name => { const index = requests.findIndex(row => row.name === name); assert.notEqual(index, -1, name); return requests.splice(index, 1)[0]; };
  return { store: exports.useAgentTrialReviewStore, calls,
    resolve: (name, value) => take(name).resolve(value), reject: (name, status = 0) => take(name).reject(new APIError(status)),
    changeUser: next => { const before = { user }; user = next; listeners.forEach(fn => fn({ user }, before)); },
  };
}
const caseSpec = { id: 'knowledge.v1.1', template_id: 'knowledge', template_version: 1,
  criteria_version: 1, question: '这款包是什么材质？', expected_result: 'grounded_answer' };
const checks = { answer_correct: true, evidence_checked: true, no_unverified_claims: true, expected_behavior: true };
const applicability = { scope: 'agent_configuration_and_knowledge', status: 'current', checked_at: null, issues: [] };
const state = changes => ({ agent_id: 'agent-a', report_id: 'report-a', scope: 'knowledge_case', can_enable: false,
  available_cases: [caseSpec], applicability, can_review: true, valid_until: '2099-01-01T00:00:00Z', reviews: [], ...changes });
const receipt = changes => ({ id: 'review-a', revision: 1, request_key: 'key-1', agent_id: 'agent-a', report_id: 'report-a', reviewed_by: 'staff-a',
  scope: 'knowledge_case', can_enable: false, case: caseSpec, checks, decision: 'passed', note: '', case_status: 'current',
  applicability, superseded: false, effective: true, created_at: '2026-10-11T00:00:00Z', valid_until: '2099-01-01T00:00:00Z', ...changes });
async function select(f, value = state()) {
  const loading = f.store.getState().select('agent-a', 'report-a', caseSpec.question);
  f.resolve('cases', [caseSpec]); f.resolve('state', value); await loading;
}

test('catalog and report state preserve separate case review and employee activation', async () => {
  const f = harness(); await select(f);
  assert.equal(f.store.getState().ready, true);
  assert.equal(f.store.getState().state.can_enable, false);
  assert.equal(f.store.getState().catalog[0].id, caseSpec.id);
});

for (const variant of ['wrong_agent', 'wrong_report', 'enable', 'foreign_review', 'wrong_case']) {
  test(`rejects ${variant} in the review read response`, async () => {
    const f = harness(); const value = state();
    if (variant === 'wrong_agent') value.agent_id = 'foreign';
    if (variant === 'wrong_report') value.report_id = 'foreign';
    if (variant === 'enable') value.can_enable = true;
    if (variant === 'foreign_review') value.reviews = [receipt({ reviewed_by: 'foreign' })];
    if (variant === 'wrong_case') value.available_cases = [{ ...caseSpec, question: 'different' }];
    await select(f, value);
    assert.equal(f.store.getState().ready, false);
    assert.equal(f.store.getState().error, 'invalidResponse');
    assert.equal(f.store.getState().state, null);
  });
}

test('incomplete human checks or a noncurrent report cannot submit a pass', async () => {
  const f = harness(); await select(f, state({ can_review: false }));
  await f.store.getState().submit(caseSpec.id, 'passed', checks, '');
  assert.equal(f.calls.filter(row => row.name === 'submit').length, 0);
  const g = harness(); await select(g);
  await g.store.getState().submit(caseSpec.id, 'passed', { ...checks, evidence_checked: false }, '');
  assert.equal(g.calls.filter(row => row.name === 'submit').length, 0);
});

test('unknown submission blocks a new judgment and retries the identical original request after reading history', async () => {
  const f = harness(); await select(f);
  const first = f.store.getState().submit(caseSpec.id, 'passed', checks, '核对资料');
  const original = JSON.stringify(f.calls.find(row => row.name === 'submit').args[2]);
  f.reject('submit'); await first;
  assert.equal(f.store.getState().unknown, true);
  await f.store.getState().submit(caseSpec.id, 'rejected', checks, 'new');
  assert.equal(f.calls.filter(row => row.name === 'submit').length, 1);
  const retry = f.store.getState().retry();
  f.resolve('cases', [caseSpec]);
  f.resolve('state', state()); await new Promise(resolve => setImmediate(resolve));
  assert.equal(JSON.stringify(f.calls.filter(row => row.name === 'submit')[1].args[2]), original);
  f.resolve('submit', receipt()); await new Promise(resolve => setImmediate(resolve));
  f.resolve('cases', [caseSpec]);
  f.resolve('state', state({ reviews: [receipt()] })); await retry;
  assert.equal(f.store.getState().pending, null);
  assert.equal(f.store.getState().unknown, false);
});

test('recovery finds a persisted original receipt without resubmitting', async () => {
  const f = harness(); await select(f);
  const submitting = f.store.getState().submit(caseSpec.id, 'passed', checks, '');
  f.reject('submit'); await submitting;
  const recovering = f.store.getState().refresh();
  f.resolve('cases', [caseSpec]);
  f.resolve('state', state({ reviews: [receipt()] })); await recovering;
  assert.equal(f.store.getState().pending, null);
  assert.equal(f.calls.filter(row => row.name === 'submit').length, 1);
});

test('a competing newer judgment returns a conflict and requires rereading before another decision', async () => {
  const f = harness(); await select(f, state({ reviews: [receipt({ request_key: 'older-key' })] }));
  const submitting = f.store.getState().submit(caseSpec.id, 'rejected', checks, '重新核验');
  assert.equal(f.calls.find(row => row.name === 'submit').args[2].expected_latest_review_id, 'review-a');
  f.reject('submit', 409); await submitting;
  assert.equal(f.store.getState().pending, null);
  assert.equal(f.store.getState().ready, false);
  assert.equal(f.store.getState().error, 'conflict');
});

test('changing project or revoking administrator permission aborts requests and discards private review state', async () => {
  const f = harness(); await select(f);
  const submitting = f.store.getState().submit(caseSpec.id, 'passed', checks, '');
  const signal = f.calls.find(row => row.name === 'submit').args[3];
  f.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  assert.equal(signal.aborted, true);
  f.resolve('submit', receipt()); await submitting;
  assert.equal(f.store.getState().state, null);
  assert.equal(f.store.getState().pending, null);
  assert.equal(f.store.getState().identity, null);
  await f.store.getState().select('agent-a', 'report-a', caseSpec.question);
  assert.equal(f.calls.filter(row => row.name === 'state').length, 1);
});

test('leaving a report preserves an unknown request in memory and resolves it when revisiting', async () => {
  const f = harness(); await select(f);
  const submitting = f.store.getState().submit(caseSpec.id, 'passed', checks, '');
  f.store.getState().leave('agent-a'); f.resolve('submit', receipt()); await submitting;
  await select(f, state({ reviews: [receipt()] }));
  assert.equal(f.store.getState().pending, null);
  assert.equal(f.store.getState().state.reviews[0].id, 'review-a');
});

test('an old project response cannot update a newly selected project', async () => {
  const f = harness();
  const first = f.store.getState().select('agent-a', 'report-a', caseSpec.question);
  f.changeUser({ id: 'staff-a', project_id: 'project-b', role: 'admin' });
  const second = f.store.getState().select('agent-b', 'report-b', caseSpec.question);
  f.resolve('cases', [caseSpec]); f.resolve('state', state()); await first;
  assert.equal(f.store.getState().state, null);
  f.resolve('cases', [caseSpec]); f.resolve('state', state({ agent_id: 'agent-b', report_id: 'report-b' })); await second;
  assert.equal(f.store.getState().state.agent_id, 'agent-b');
  assert.equal(f.store.getState().identity, 'project-b:staff-a');
});

test('a failed refresh preserves history but disables another judgment', async () => {
  const f = harness(); await select(f, state({ reviews: [receipt()] }));
  const refreshing = f.store.getState().refresh(); f.resolve('cases', [caseSpec]); f.reject('state', 503); await refreshing;
  assert.equal(f.store.getState().ready, false);
  assert.equal(f.store.getState().state.reviews[0].id, 'review-a');
  await f.store.getState().submit(caseSpec.id, 'passed', checks, '');
  assert.equal(f.calls.filter(row => row.name === 'submit').length, 0);
});

test('refresh rereads changed template criteria and removes old review effectiveness', async () => {
  const f = harness(); await select(f, state({ reviews: [receipt()] }));
  const nextCase = { ...caseSpec, expected_result: 'safe_refusal' };
  const refreshing = f.store.getState().refresh();
  f.resolve('cases', [nextCase]);
  f.resolve('state', state({ available_cases: [nextCase], can_review: false,
    reviews: [receipt({ case_status: 'changed', effective: false })] }));
  await refreshing;
  assert.equal(f.store.getState().ready, true);
  assert.equal(f.store.getState().catalog[0].expected_result, 'safe_refusal');
  assert.equal(f.store.getState().state.reviews[0].effective, false);
});
