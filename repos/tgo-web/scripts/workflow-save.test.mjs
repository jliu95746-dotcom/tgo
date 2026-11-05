import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const requests = [];
  const api = {
    updateWorkflow: (id, data) => new Promise((resolve, reject) => requests.push({ id, data, resolve, reject })),
    listWorkflows: async () => ({ data: [] }),
  };
  const imports = {
    '@/services/workflowApi': { WorkflowApiService: api },
    '@/types/workflow': { DEFAULT_NODE_DATA: {} },
    '@/utils/workflowTransforms': { migrateNodes: nodes => nodes },
    '@/i18n': { __esModule: true, default: { t: key => key } },
    './authStore': { useAuthStore: { getState: () => ({ user }) } },
    './workflowExecutionActions': { createWorkflowExecutionActions: () => ({}) },
    'zustand/middleware': { devtools: value => value, persist: value => value }, reactflow: {},
    zustand: { create: () => initializer => {
      let state;
      const set = update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) }; };
      state = initializer(set, () => state);
      return { getState: () => state, setState: set };
    } },
  };
  function load(path) {
    const exports = {};
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
    }).outputText, { exports, console: { error() {} }, require: name => {
      if (name === './workflowSaveActions') return load('stores/workflowSaveActions.ts');
      if (name in imports) return imports[name];
      throw new Error(name);
    } });
    return exports;
  }
  const store = load('stores/workflowStore.ts').useWorkflowStore;
  const workflow = (id = 'flow-a', name = '资料咨询') => ({ id, name, version: 1, tags: [], status: 'draft', definition: { nodes: [], edges: [] } });
  store.getState().setCurrentWorkflow(workflow());
  store.getState().updateWorkflowMeta({ name: '已修改的资料咨询' });
  const resolve = (index, changes = {}) => {
    const row = requests[index];
    row.resolve({ ...workflow(row.id), name: row.data.name, description: row.data.description, tags: row.data.tags,
      definition: { nodes: row.data.nodes, edges: row.data.edges }, version: row.data.expected_version + 1, ...changes });
  };
  return { store, requests, resolve, workflow, changeUser: next => { user = next; } };
}

test('save then immediately run shares one durable draft save and one resulting version', async () => {
  const h = fixture();
  const first = h.store.getState().saveWorkflow(), second = h.store.getState().saveWorkflow();
  const count = h.requests.length;
  h.requests.forEach((_, index) => h.resolve(index));
  await Promise.all([first, second]);
  assert.equal(count, 1);
  assert.equal(h.store.getState().currentWorkflow.version, 2);
  assert.equal(h.store.getState().isDirty, false);
});

test('edits made while saving remain dirty and use the confirmed version on the next save', async () => {
  const h = fixture();
  const first = h.store.getState().saveWorkflow();
  h.store.getState().updateWorkflowMeta({ name: '保存期间继续输入' });
  h.store.getState().setNodes([{ id: 'later', type: 'answer', data: {} }]);
  h.resolve(0); await first;
  assert.equal(h.store.getState().currentWorkflow.name, '保存期间继续输入');
  assert.equal(h.store.getState().currentWorkflow.definition.nodes[0].id, 'later');
  assert.equal(h.store.getState().currentWorkflow.version, 2);
  assert.equal(h.store.getState().isDirty, true);
  const next = h.store.getState().saveWorkflow();
  assert.equal(h.requests[1].data.expected_version, 2);
  h.resolve(1); await next;
  assert.equal(h.store.getState().isDirty, false);
});

test('an old save cannot replace a newly opened editor or finish its pending test', async () => {
  const h = fixture();
  const first = h.store.getState().saveWorkflow();
  h.store.getState().setCurrentWorkflow(h.workflow('flow-b', '另一个流程'));
  h.resolve(0);
  assert.equal((await Promise.allSettled([first]))[0].status, 'rejected');
  assert.equal(h.store.getState().currentWorkflow.id, 'flow-b');
});

test('changing staff, project or role while saving prevents the old response being applied', async () => {
  for (const next of [
    { id: 'staff-b', project_id: 'project-a', role: 'admin' },
    { id: 'staff-a', project_id: 'project-b', role: 'admin' },
    { id: 'staff-a', project_id: 'project-a', role: 'agent' },
  ]) {
    const h = fixture();
    const before = h.store.getState().currentWorkflow;
    const first = h.store.getState().saveWorkflow();
    h.changeUser(next); h.resolve(0);
    assert.equal((await Promise.allSettled([first]))[0].status, 'rejected');
    assert.equal(h.store.getState().currentWorkflow, before);
  }
});

test('an unknown save outcome is shared by callers without automatic retry or a new version', async () => {
  const h = fixture();
  const first = h.store.getState().saveWorkflow(), second = h.store.getState().saveWorkflow();
  const count = h.requests.length;
  h.requests.forEach(row => row.reject(new Error('network outcome unknown')));
  const settled = await Promise.allSettled([first, second]);
  assert.ok(settled.every(row => row.status === 'rejected'));
  assert.equal(count, 1);
  assert.equal(h.store.getState().currentWorkflow.version, 1);
  assert.equal(h.store.getState().isDirty, true);
  const next = h.store.getState().saveWorkflow();
  assert.equal(h.requests.at(-1).data.expected_version, 1);
  h.resolve(h.requests.length - 1); await next;
});

test('list refresh failure does not reject an already confirmed draft save', async () => {
  const h = fixture();
  h.store.setState({ refreshWorkflows: async () => { throw new Error('list unavailable'); } });
  const first = h.store.getState().saveWorkflow();
  h.resolve(0);
  assert.equal((await Promise.allSettled([first]))[0].status, 'fulfilled');
  assert.equal(h.store.getState().currentWorkflow.version, 2);
});

test('foreign or non-advancing save receipts cannot mark the draft clean', async () => {
  for (const changes of [{ id: 'flow-b' }, { version: 1 }]) {
    const h = fixture();
    const first = h.store.getState().saveWorkflow();
    h.resolve(0, changes);
    assert.equal((await Promise.allSettled([first]))[0].status, 'rejected');
    assert.equal(h.store.getState().currentWorkflow.version, 1);
    assert.equal(h.store.getState().isDirty, true);
  }
});
