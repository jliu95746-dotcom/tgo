import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const moduleRequire = createRequire(import.meta.url);
const i18n = { t: (key, fallback) => fallback ?? key };

function loadSource(path, imports) {
  const scope = {
    exports: {}, AbortController, Error, Date, Promise,
    console: { error() {}, warn() {} },
    require: name => imports[name] ?? moduleRequire(name),
  };
  vm.runInNewContext(ts.transpileModule(
    readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true,
      target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } },
  ).outputText, scope);
  return scope.exports;
}

function loadStore(api) {
  const actions = loadSource('stores/workflowExecutionActions.ts', {
    '@/services/workflowApi': { WorkflowApiService: api }, '@/i18n': i18n,
  });
  const { useWorkflowStore } = loadSource('stores/workflowStore.ts', {
    '@/services/workflowApi': { WorkflowApiService: api },
    '@/types/workflow': { DEFAULT_NODE_DATA: {} },
    '@/utils/workflowTransforms': {}, '@/i18n': i18n,
    './workflowExecutionActions': actions,
    'zustand/middleware': { devtools: value => value, persist: value => value },
    reactflow: {},
  });
  useWorkflowStore.setState({ currentWorkflow: { id: 'workflow-owned' } });
  return useWorkflowStore;
}

const started = id => ({ event: 'workflow_started', workflow_run_id: id,
  data: { id, workflow_id: 'workflow-owned', inputs: {}, created_at: 1 } });
const finished = (id, status) => ({ event: 'workflow_finished', workflow_run_id: id,
  data: { status, outputs: null, elapsed_time: 1 } });

test('an incomplete stream rejects instead of leaving the spinner running', async () => {
  const { WorkflowApiService } = loadSource('services/workflowApi.ts', {
    './api': { stream: async () => {} }, '@/i18n': i18n,
  });
  await assert.rejects(WorkflowApiService.executeWorkflowStream('owned', {}, () => {}), /中断/);
});

test('cancelled terminal events preserve cancelled workflow and node states', async () => {
  const store = loadStore({ executeWorkflowStream: async (_id, _input, event) => {
    event(started('run-owned'));
    event({ event: 'node_started', workflow_run_id: 'run-owned',
      data: { id: 'node-run', node_id: 'node', node_type: 'api' } });
    event(finished('run-owned', 'cancelled'));
  } });
  await store.getState().startExecution({});
  assert.equal(store.getState().currentExecution.status, 'cancelled');
  assert.equal(store.getState().nodeExecutionMap.node.status, 'cancelled');
  assert.equal(store.getState().isExecuting, false);
});

test('cancelling before the first event clears the pending execution', async () => {
  const store = loadStore({ executeWorkflowStream: (_id, _input, _event, signal) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  }) });
  const startedRun = store.getState().startExecution({});
  await store.getState().cancelExecution();
  await startedRun;
  assert.equal(store.getState().isExecuting, false);
  assert.equal(store.getState().executionAbortController, null);
});

test('late events from a replaced stream cannot overwrite the current run', async () => {
  const calls = [];
  const store = loadStore({ executeWorkflowStream: (_id, _input, event) => new Promise(resolve => calls.push({ event, resolve })) });
  const first = store.getState().startExecution({});
  const second = store.getState().startExecution({});
  calls[1].event(started('run-new'));
  calls[0].event(started('run-old'));
  calls[0].event(finished('run-old', 'succeeded'));
  assert.equal(store.getState().currentExecution.id, 'run-new');
  assert.equal(store.getState().isExecuting, true);
  calls[1].event(finished('run-new', 'succeeded'));
  calls.forEach(call => call.resolve());
  await Promise.all([first, second]);
});

test('a failed cancellation is visible and never pretends to be confirmed', async () => {
  const store = loadStore({
    cancelExecution: async () => { throw new Error('HTTP 503'); },
    getExecution: async () => ({ id: 'run-owned', status: 'running' }),
  });
  store.setState({ currentExecution: { id: 'run-owned', status: 'running' }, isExecuting: true });
  await store.getState().cancelExecution();
  assert.ok(store.getState().executionError);
  assert.equal(store.getState().currentExecution.status, 'running');
});

test('clearing the debug run aborts its stream', () => {
  const store = loadStore({});
  const controller = new AbortController();
  store.setState({ executionAbortController: controller, isExecuting: true });
  store.getState().clearExecution();
  assert.equal(controller.signal.aborted, true);
  assert.equal(store.getState().executionAbortController, null);
});

test('completion racing cancellation is reconciled from the server', async () => {
  const store = loadStore({
    cancelExecution: async () => { throw new Error('HTTP 400: already completed'); },
    getExecution: async () => ({ id: 'run-owned', status: 'completed', node_executions: [] }),
  });
  store.setState({ currentExecution: { id: 'run-owned', status: 'running' } });
  await store.getState().cancelExecution();
  assert.equal(store.getState().currentExecution.status, 'completed');
  assert.equal(store.getState().executionError, null);
});

test('resetting the editor aborts its active debug stream', () => {
  const store = loadStore({});
  const controller = new AbortController();
  store.setState({ executionAbortController: controller });
  store.getState().resetEditor();
  assert.equal(controller.signal.aborted, true);
});

test('debug panel displays cancellation and incomplete errors in Chinese', () => {
  for (const status of ['cancelled', 'no-first-event']) {
    const { default: DebugPanel } = loadSource('components/workflow/panels/DebugPanel.tsx', {
      '@/stores/workflowStore': { useWorkflowStore: () => ({
        currentWorkflow: null, isExecuting: false,
        currentExecution: status === 'cancelled' ? { status, node_executions: [] } : null,
        executionError: status === 'no-first-event' ? '调试连接已中断' : null,
        nodeExecutionMap: {}, debugInput: {},
      }) },
      'react-i18next': { useTranslation: () => i18n },
    });
    const html = renderToStaticMarkup(React.createElement(DebugPanel));
    assert.ok(html.includes(status === 'cancelled' ? '已取消' : '执行未完成'));
    assert.ok(!html.includes('animate-spin'));
  }
});

test('a late cancellation response cannot stop a newer debug run', async () => {
  let finishCancel;
  let finishRun;
  let sendEvent;
  const store = loadStore({
    cancelExecution: () => new Promise(resolve => { finishCancel = resolve; }),
    executeWorkflowStream: (_id, _input, event) => new Promise(resolve => {
      sendEvent = event;
      finishRun = resolve;
    }),
  });
  store.setState({ currentExecution: { id: 'run-old', status: 'running' } });
  const cancellation = store.getState().cancelExecution();
  const running = store.getState().startExecution({});
  sendEvent(started('run-new'));
  finishCancel();
  await cancellation;
  assert.equal(store.getState().currentExecution.id, 'run-new');
  assert.equal(store.getState().isExecuting, true);
  sendEvent(finished('run-new', 'succeeded'));
  finishRun();
  await running;
});
