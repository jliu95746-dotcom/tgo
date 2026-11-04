import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const nativeRequire = createRequire(import.meta.url);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const workflow = (id, name = id) => ({ id, name, version: 1, tags: [], status: 'draft',
  definition: { nodes: [], edges: [] }, created_at: '2026-10-25T00:00:00Z' });

function load(path, imports) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      esModuleInterop: true, jsx: ts.JsxEmit.React },
  }).outputText, { exports, AbortController, console, require: name => imports[name] ?? nativeRequire(name) });
  return exports;
}

function storeFixture() {
  const requests = [];
  const api = { getWorkflow: id => {
    const request = { id, ...deferred() }; requests.push(request); return request.promise;
  } };
  const { useWorkflowStore: store } = load('stores/workflowStore.ts', {
    '@/services/workflowApi': { WorkflowApiService: api },
    '@/types/workflow': { DEFAULT_NODE_DATA: {} },
    '@/utils/workflowTransforms': { migrateNodes: nodes => nodes },
    '@/i18n': { t: key => key }, './workflowExecutionActions': { createWorkflowExecutionActions: () => ({}) },
    'zustand/middleware': { devtools: value => value, persist: value => value }, reactflow: {},
  });
  return { store, requests };
}

test('a delayed load preserves a name and graph edited while waiting', async () => {
  const h = storeFixture();
  h.store.getState().setCurrentWorkflow(workflow('flow-a', '原名称'));
  const pending = h.store.getState().loadWorkflow('flow-a');
  h.store.getState().updateWorkflowMeta({ name: '客户新输入的名称' });
  h.store.getState().setNodes([{ id: 'new-node', type: 'answer', data: {} }]);
  const edited = h.store.getState().currentWorkflow;
  h.requests[0].resolve(workflow('flow-a', '服务器旧名称'));
  await pending;
  assert.equal(h.store.getState().currentWorkflow, edited);
  assert.equal(h.store.getState().isDirty, true);
  assert.equal(h.store.getState().isLoadingCurrentWorkflow, false);
});

test('an older workflow response cannot replace a newer opened workflow', async () => {
  const h = storeFixture();
  const first = h.store.getState().loadWorkflow('flow-a');
  const second = h.store.getState().loadWorkflow('flow-b');
  h.requests[1].resolve(workflow('flow-b')); await second;
  h.requests[0].resolve(workflow('flow-a')); await first;
  assert.equal(h.store.getState().currentWorkflow.id, 'flow-b');
});

test('an older error cannot overwrite the loading state of a new request', async () => {
  const h = storeFixture();
  const first = h.store.getState().loadWorkflow('flow-a');
  const second = h.store.getState().loadWorkflow('flow-b');
  h.requests[0].reject(new Error('HTTP 503 from old request')); await first;
  assert.equal(h.store.getState().isLoadingCurrentWorkflow, true);
  assert.equal(h.store.getState().currentWorkflowError, null);
  h.requests[1].resolve(workflow('flow-b')); await second;
});

for (const action of ['resetEditor', 'closeWorkflowEditor', 'setCurrentWorkflow']) {
  test(`${action} invalidates an in-flight workflow read`, async () => {
    const h = storeFixture();
    const pending = h.store.getState().loadWorkflow('flow-a');
    if (action === 'setCurrentWorkflow') h.store.getState()[action](workflow('flow-b'));
    else h.store.getState()[action]();
    h.requests[0].resolve(workflow('flow-a')); await pending;
    assert.equal(h.store.getState().currentWorkflow?.id ?? null, action === 'setCurrentWorkflow' ? 'flow-b' : null);
    assert.equal(h.store.getState().isLoadingCurrentWorkflow, false);
  });
}

test('an ordinary load still initializes the current graph and history', async () => {
  const h = storeFixture();
  const pending = h.store.getState().loadWorkflow('flow-a');
  h.requests[0].resolve(workflow('flow-a')); await pending;
  assert.equal(h.store.getState().currentWorkflow.id, 'flow-a');
  assert.equal(h.store.getState().history.length, 1);
  assert.equal(h.store.getState().isDirty, false);
});

test('opening another workflow is not blocked by the previous workflow dirty flag', async () => {
  const h = storeFixture();
  h.store.getState().setCurrentWorkflow(workflow('flow-a'));
  h.store.getState().updateWorkflowMeta({ name: '旧流程尚未保存' });
  const pending = h.store.getState().loadWorkflow('flow-b');
  h.requests[0].resolve(workflow('flow-b')); await pending;
  assert.equal(h.store.getState().currentWorkflow.id, 'flow-b');
});

function renderPage(id, current, loading = false, error = null) {
  const state = { currentWorkflow: current, isLoadingCurrentWorkflow: loading, isDirty: false,
    currentWorkflowError: error,
    loadWorkflow() {}, createWorkflow() {}, updateWorkflowMeta() {}, resetEditor() {}, setSelectedNode() {}, setDebugPanelOpen() {} };
  const react = { ...React, useEffect() {}, useState: initial => [typeof initial === 'function' ? false : initial, () => {}],
    useMemo: factory => factory(), useRef: value => ({ current: value }) };
  const { default: Page } = load('pages/WorkflowEditorPage.tsx', {
    react, 'react-i18next': { useTranslation: () => ({ t: (_key, fallback) => fallback ?? _key }) },
    'react-router-dom': { useParams: () => ({ id }), useNavigate: () => () => {} },
    '@/stores/workflowStore': { useWorkflowStore: () => state }, '@/hooks/useToast': { useToast: () => ({ showToast() {} }) },
    '@/components/workflow': { WorkflowEditor: () => React.createElement('div', { 'data-testid': 'editable-canvas' }),
      WorkflowToolbar: () => React.createElement('button', null, '保存') },
    '@/components/workflow/NodePalette': { __esModule: true, default: () => null },
    '@/components/workflow/panels/NodeConfigPanel': { __esModule: true, default: () => null },
    '@/components/workflow/panels/DebugPanel': { __esModule: true, default: () => null },
    '@/components/business/WorkflowBasicConfiguration': { __esModule: true, default: () => null },
  });
  return renderToStaticMarkup(React.createElement(Page));
}

for (const [description, id, current, loading] of [
  ['new workflow navigation', 'new', workflow('flow-created'), false],
  ['an active workflow load', 'flow-a', workflow('flow-a'), true],
  ['a route still showing the previous flow', 'flow-b', workflow('flow-a'), false],
]) {
  test(`${description} does not expose inputs or draft actions before ready`, () => {
    const html = renderPage(id, current, loading);
    assert.equal(html.includes('<input'), false);
    assert.equal(html.includes('editable-canvas'), false);
    assert.equal(html.includes('>保存<'), false);
  });
}

test('a ready editor retains the existing name and canvas controls', () => {
  const html = renderPage('flow-a', workflow('flow-a', '可编辑的流程'));
  assert.ok(html.includes('可编辑的流程'));
  assert.ok(html.includes('editable-canvas'));
  assert.ok(html.includes('>保存<'));
});

test('a failed read displays the original HTTP error and a retry instead of editable stale data', () => {
  const html = renderPage('flow-a', null, false, 'HTTP 503 Service Unavailable');
  assert.ok(html.includes('role="alert"'));
  assert.ok(html.includes('HTTP 503 Service Unavailable'));
  assert.ok(html.includes('>重试<'));
  assert.equal(html.includes('editable-canvas'), false);
});
