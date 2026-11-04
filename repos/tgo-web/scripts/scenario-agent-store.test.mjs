import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function harness() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const listeners = [], calls = [], pending = [];
  const input = { template_id: 'knowledge', module: 'agents', template_version: 1, channel: 'web', step: 'testing', business: { name: '客服' }, bindings: {}, agent_settings: {} };
  let current = { ...input, id: 'draft-a', project_id: user.project_id, created_by: user.id, revision: 2 };
  let saves = 0;
  const auth = { getState: () => ({ user }), subscribe: fn => listeners.push(fn) };
  const drafts = { getState: () => ({ current, save: async value => { saves++; current = { ...current, ...value, revision: current.revision + 1 }; return true; } }) };
  const create = initializer => { let state; const set = next => { state = { ...state, ...next }; }; state = initializer(set, () => state); return { getState: () => state, setState: set }; };
  const api = { saveDraftAgent: (...args) => { calls.push(args); return new Promise((resolve, reject) => pending.push({ resolve, reject })); } };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/stores/scenarioAgentStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, AbortController, JSON, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: auth };
    if (name === './scenarioDraftStore') return { useScenarioDraftStore: drafts };
    if (name === '@/services/businessScenariosApi') return { BusinessScenariosApi: api };
    throw new Error(name);
  } });
  return { store: exports.useScenarioAgentStore, input, calls, saves: () => saves,
    receipt: () => ({ draft_id: current.id, project_id: user.project_id, created_by: user.id, revision: current.revision, agent_id: 'agent-a', is_active: false, can_enable: false }),
    resolve: value => pending.shift().resolve(value), reject: () => pending.shift().reject(new Error('network')),
    changeUser: next => { const previous = { user }; user = next; listeners.forEach(fn => fn({ user }, previous)); },
  };
}

test('double submission saves one employee snapshot, with no duplicate draft revision', async () => {
  const h = harness();
  const first = h.store.getState().prepare(h.input);
  assert.equal(await h.store.getState().prepare(h.input), null);
  assert.equal(h.saves(), 0);
  h.resolve(h.receipt());
  assert.equal((await first).agent_id, 'agent-a');
  assert.equal(h.calls.length, 1);
});

test('unknown outcome retries the same durable draft and revision', async () => {
  const h = harness();
  const first = h.store.getState().prepare(h.input);
  h.reject();
  assert.equal(await first, null);
  const second = h.store.getState().prepare(h.input);
  assert.deepEqual(h.calls[0].slice(0, 2), h.calls[1].slice(0, 2));
  h.resolve(h.receipt());
  await second;
  assert.equal(h.saves(), 0);
});

test('identity and role changes abort old materialization and ignore its result', async () => {
  const h = harness();
  const first = h.store.getState().prepare(h.input);
  h.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  assert.equal(h.calls[0][2].aborted, true);
  h.resolve({ ...h.receipt(), created_by: 'staff-a' });
  assert.equal(await first, null);
  assert.equal(h.store.getState().receipt, null);
  assert.equal(await h.store.getState().prepare(h.input), null);
});

test('a changed form saves first and materializes the new revision of the same draft', async () => {
  const h = harness();
  const first = h.store.getState().prepare({ ...h.input, business: { name: '新客服' } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.saves(), 1);
  assert.equal(h.calls[0][1], 3);
  h.resolve(h.receipt());
  assert.equal((await first).revision, 3);
});

test('foreign, active or enabling receipts are rejected', async () => {
  for (const change of [{ project_id: 'other' }, { created_by: 'other' }, { draft_id: 'other' }, { revision: 999 }, { is_active: true }, { can_enable: true }]) {
    const h = harness();
    const first = h.store.getState().prepare(h.input);
    h.resolve({ ...h.receipt(), ...change });
    assert.equal(await first, null);
    assert.equal(h.store.getState().receipt, null);
  }
});
