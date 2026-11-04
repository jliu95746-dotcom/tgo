import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function harness() {
  let user = { id: 'admin-a', project_id: 'project-a', role: 'admin' };
  const listeners = [], calls = [], pending = [];
  class APIError extends Error { constructor(status) { super('synthetic'); this.status = status; } }
  const api = new Proxy({}, { get: (_, name) => (...args) => {
    calls.push({ name, args });
    return new Promise((resolve, reject) => pending.push({ name, resolve, reject }));
  } });
  const exports = {};
  let key = 0;
  const create = initialize => {
    let state;
    const set = patch => { state = { ...state, ...patch }; };
    state = initialize(set, () => state);
    return { getState: () => state, setState: set };
  };
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/agentKnowledgeCatalogStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, AbortController, crypto: { randomUUID: () => `key-${++key}` }, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: { getState: () => ({ user }), subscribe: fn => listeners.push(fn) } };
    if (name === '@/services/api') return { APIError };
    if (name === '@/services/agentTrialReviewsApi') return { AgentTrialReviewsApi: api };
    throw new Error(name);
  } });
  const take = name => { const index = pending.findIndex(row => row.name === name); assert.notEqual(index, -1); return pending.splice(index, 1)[0]; };
  return { store: exports.useAgentKnowledgeCatalogStore, calls,
    resolve: (name, value) => take(name).resolve(value),
    reject: (name, status) => take(name).reject(new APIError(status)),
    changeUser: next => { const previous = { user }; user = next; listeners.forEach(fn => fn({ user }, previous)); },
  };
}
const cases = Array.from({ length: 6 }, (_, index) => ({
  id: `case-${index}`, question: `Business question ${index}`, template_id: index < 3 ? 'knowledge' : 'aftersales',
  template_version: 1, criteria_version: 1, expected_result: index % 3 === 2 ? 'safe_refusal' : 'grounded_answer',
}));
const catalog = (changes = {}) => ({ agent_id: 'agent-a', id: null, revision: 0, request_key: null,
  configured_by: null, created_at: null, current: true, cases, ...changes });
async function load(f) { const promise = f.store.getState().load('agent-a'); f.resolve('catalog', catalog()); await promise; f.store.getState().open(); }
function receipt(f) {
  const request = f.calls.filter(row => row.name === 'saveCatalog').at(-1).args[1];
  return catalog({ id: 'version-one', request_key: request.request_key, revision: 1,
    cases: cases.map((row, index) => ({ ...row, id: `version-one-${index}`, question: request.questions[index] })) });
}

test('custom questions save only to the selected employee and receive a new immutable version', async () => {
  const f = harness(); await load(f);
  f.store.getState().change(0, 'Which services do we offer?');
  const promise = f.store.getState().save(); f.resolve('saveCatalog', receipt(f));
  assert.equal(await promise, true);
  assert.equal(f.calls.at(-1).args[0], 'agent-a');
  assert.equal(f.store.getState().catalog.revision, 1);
  assert.equal(f.store.getState().editing, false);
});

test('an unknown save freezes editing and reuses the exact request key and body', async () => {
  const f = harness(); await load(f);
  const saving = f.store.getState().save(); f.reject('saveCatalog', 0); await saving;
  f.store.getState().change(0, 'Must not replace pending content');
  assert.equal(f.store.getState().questions[0], cases[0].question);
  const retry = f.store.getState().save();
  const writes = f.calls.filter(row => row.name === 'saveCatalog');
  assert.equal(writes.length, 2); assert.equal(writes[0].args[1], writes[1].args[1]);
  f.resolve('saveCatalog', receipt(f)); assert.equal(await retry, true);
});

test('a competing version produces a conflict without overwriting questions', async () => {
  const f = harness(); await load(f);
  const saving = f.store.getState().save(); f.reject('saveCatalog', 409); await saving;
  assert.equal(f.store.getState().error, 'conflict');
  assert.equal(f.store.getState().pending, null);
  assert.equal(f.store.getState().catalog.revision, 0);
});

test('changing project or revoking admin discards drafts and ignores a late save receipt', async () => {
  const f = harness(); await load(f);
  const saving = f.store.getState().save(); const value = receipt(f);
  f.changeUser({ id: 'admin-a', project_id: 'project-b', role: 'user' });
  f.resolve('saveCatalog', value); assert.equal(await saving, false);
  assert.equal(f.store.getState().catalog, null); assert.equal(f.store.getState().pending, null);
});

test('foreign employee catalogs and modified evaluation rules fail closed', async () => {
  for (const bad of [catalog({ agent_id: 'foreign' }), catalog({ cases: cases.map(row => ({ ...row, expected_result: 'passed' })) })]) {
    const f = harness(); const loading = f.store.getState().load('agent-a'); f.resolve('catalog', bad); await loading;
    assert.equal(f.store.getState().catalog, null); assert.equal(f.store.getState().error, 'invalidResponse');
  }
});
