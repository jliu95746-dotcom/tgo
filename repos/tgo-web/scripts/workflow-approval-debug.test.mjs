import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture(mode = 'debug') {
  const calls = [];
  const snapshots = [
    { id: 'run-a', workflow_id: 'flow-a', status: 'waiting_approval', node_executions: [
      { id: 'node-run-a', execution_id: 'run-a', node_id: 'approval-a', node_type: 'approval', status: 'completed' },
    ] },
    { id: 'run-a', workflow_id: 'flow-a', status: 'completed', node_executions: [], output: '继续执行完成' },
  ];
  const api = {
    executeWorkflowAsync: async (id, input) => {
      calls.push({ method: 'async', id, input });
      return { id: 'run-a', workflow_id: id, status: 'pending', node_executions: [] };
    },
    executeWorkflowStream: async () => { throw Error('approval flow must not use SSE'); },
    getExecution: async () => { calls.push({ method: 'get' }); return snapshots.shift(); },
    cancelExecution: async id => { calls.push({ method: 'cancel', id }); },
  };
  const state = {
    currentWorkflow: { id: 'flow-a', definition: { nodes: [{ id: 'approval-a', type: 'approval' }] } },
    currentExecution: null, isExecuting: false, executionError: null, executionPanelMode: mode,
    nodeExecutionMap: {}, executionAbortController: null,
  };
  const exports = {};
  const source = readFileSync(new URL('../src/stores/workflowExecutionActions.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  } }).outputText, {
    exports, AbortController, Date, Error, Object, Promise,
    setTimeout: callback => callback(),
    require: name => {
      if (name === '@/services/workflowApi') return { WorkflowApiService: api };
      if (name === '@/i18n') return { default: { t: (_key, fallback) => fallback } };
      throw Error(`Unexpected dependency ${name}`);
    },
  });
  const set = update => Object.assign(state, typeof update === 'function' ? update(state) : update);
  return { actions: exports.createWorkflowExecutionActions(set, () => state), state, calls };
}

test('approval debug uses async execution, pauses polling, then refreshes the persisted result', async () => {
  const { actions, state, calls } = fixture();
  await actions.startExecution({ question: '请核对结果' });
  for (let step = 0; step < 5 && state.currentExecution.status !== 'waiting_approval'; step++) {
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.equal(calls[0].method, 'async');
  assert.equal(calls[0].input.question, '请核对结果');
  assert.equal(state.currentExecution.status, 'waiting_approval');
  assert.equal(state.isExecuting, false);
  assert.equal(state.executionAbortController, null);
  assert.equal(state.nodeExecutionMap['approval-a'].node_type, 'approval');
  assert.equal(calls.filter(call => call.method === 'get').length, 1);

  await actions.refreshExecution();
  assert.equal(state.currentExecution.status, 'completed');
  assert.equal(state.currentExecution.output, '继续执行完成');
});

test('a waiting approval can be cancelled from the debug panel', async () => {
  const { actions, state, calls } = fixture();
  await actions.startExecution({});
  for (let step = 0; step < 5 && state.currentExecution.status !== 'waiting_approval'; step++) {
    await new Promise(resolve => setImmediate(resolve));
  }
  await actions.cancelExecution();
  assert.equal(state.currentExecution.status, 'cancelled');
  assert.deepEqual(calls.find(call => call.method === 'cancel'), { method: 'cancel', id: 'run-a' });
});

test('history viewing cannot start or cancel a run even through shared actions', async () => {
  const { actions, state, calls } = fixture('history');
  const controller = new AbortController();
  state.currentExecution = { id: 'existing-run', status: 'waiting_approval' };
  state.executionAbortController = controller;
  await actions.startExecution({ question: '不应发送' });
  await actions.cancelExecution();
  assert.equal(calls.length, 0);
  assert.equal(controller.signal.aborted, false);
  assert.equal(state.currentExecution.id, 'existing-run');
});

test('history status refresh preserves history mode and clearing returns to debug', async () => {
  const { actions, state, calls } = fixture('history');
  state.currentExecution = { id: 'run-a', status: 'pending' };
  await actions.refreshExecution();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'get');
  assert.equal(state.executionPanelMode, 'history');
  actions.clearExecution();
  assert.equal(state.executionPanelMode, 'debug');
  assert.equal(state.currentExecution, null);
});
