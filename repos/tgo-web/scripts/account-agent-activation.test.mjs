import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/stores/aiStore.ts', import.meta.url), 'utf8');
const tree = ts.createSourceFile('aiStore.ts', source, ts.ScriptTarget.Latest, true);
let update;
function visit(node) {
  if (ts.isPropertyAssignment(node) && node.name.getText(tree) === 'updateAgent') update = node.initializer;
  ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(update);
const code = ts.transpileModule(`globalThis.update = ${update.getText(tree)};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

function fixture({ pause = null, failRefresh = false } = {}) {
  let state = { agents: [{ id: 'old', status: 'active' }, { id: 'new', status: 'inactive' }], selectedAgent: { id: 'old', status: 'active' }, isChangingAgentActivation: false };
  let writes = 0;
  const scope = {
    get: () => state,
    set: value => { state = { ...state, ...(typeof value === 'function' ? value(state) : value) }; },
    useAuthStore: { getState: () => ({ user: { project_id: 'fixture' } }) },
    i18n: { t: key => key }, console: { error() {} },
    AIAgentsTransformUtils: { transformAgentPatch: (_agent, updates) => ({ is_active: updates.status === 'active' }), transformApiAgentToAgent: agent => agent },
    AIAgentsApiService: {
      updateAgent: async () => { writes++; if (pause) await pause; return { id: 'new', status: 'active' }; },
      getAgents: async () => { if (failRefresh) throw Error('refresh failed'); return { data: [{ id: 'old', status: 'inactive' }, { id: 'new', status: 'active' }] }; },
    },
  };
  vm.runInNewContext(code, scope);
  return { update: scope.update, state: () => state, writes: () => writes };
}

test('activation refreshes both cards and the selected employee from the server', async () => {
  const store = fixture();
  await store.update('new', { status: 'active' });
  assert.equal(store.state().agents.filter(a => a.status === 'active').length, 1);
  assert.equal(store.state().selectedAgent.status, 'inactive');
  assert.equal(store.state().isChangingAgentActivation, false);
});

test('different cards cannot start overlapping activation changes', async () => {
  let release;
  const pause = new Promise(resolve => { release = resolve; });
  const store = fixture({ pause });
  const first = store.update('new', { status: 'active' });
  assert.equal(store.state().isChangingAgentActivation, true);
  await assert.rejects(store.update('old', { status: 'inactive' }));
  assert.equal(store.writes(), 1);
  assert.equal(store.state().isChangingAgentActivation, true);
  release();
  await first;
  assert.equal(store.state().isChangingAgentActivation, false);
});

test('a failed refresh clears the pending flag and marks the list as unverified', async () => {
  const store = fixture({ failRefresh: true });
  await assert.rejects(store.update('new', { status: 'active' }));
  assert.ok(store.state().agentsError);
  assert.equal(store.state().isChangingAgentActivation, false);
});
