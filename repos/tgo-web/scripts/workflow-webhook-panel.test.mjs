import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

function component(file, mocks) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, { exports, require: name => {
    if (name in mocks) return mocks[name];
    if (name === 'react') return React;
    if (name === 'react/jsx-runtime') return jsxRuntime;
    if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key, i18n: { language: 'zh' } }) };
    if (name === 'lucide-react') return new Proxy({}, { get: () => () => null });
    throw Error(`Unexpected dependency ${name}`);
  } });
  return exports.default;
}
function render(patch = {}, role = 'admin', data = { type: 'webhook', method: 'POST' }) {
  const hook = { id: 'hook-a', workflow_id: 'flow-a', project_id: 'project-a', enabled: true,
    revision: 1, credential_revision: 1, key_id: 'key-a' };
  const state = { ownerKey: 'project-a:staff-a:admin', workflowId: 'flow-a', state: { webhook: hook, binding_current: true,
    current_release: { version: 1 }, execution_available: true, registration_available: true },
    history: { items: [], total: 0, limit: 10, offset: 0, has_more: false }, pending: null, credential: null,
    loading: false, saving: false, error: '', notice: '', historyError: '', ...patch };
  const user = { id: 'staff-a', project_id: 'project-a', role };
  const mocks = {
    '@/stores/authStore': { useAuthStore: selector => selector({ user }) },
    '@/stores/workflowStore': { useWorkflowStore: selector => selector({ currentWorkflow: { id: 'flow-a' }, isDirty: false }) },
    '@/stores/workflowWebhookStore': { useWorkflowWebhookStore: () => state },
    '@/stores/workflowEventTestStore': { workflowEventOwner: value => value?.role === 'admin' ? `${value.project_id}:${value.id}:admin` : null },
    '@/services/workflowWebhookApi': { webhookIngressUrl: id => `https://example.invalid/api/v1/workflow-webhooks/${id}` },
    '@/services/workflowApi': { WorkflowApiService: {} },
    './WorkflowScheduleConfiguration': { scheduleTime: value => value },
  };
  const History = component('components/workflow/panels/WebhookDeliveryHistory.tsx', mocks);
  const Config = component('components/workflow/panels/WebhookNodeConfig.tsx', { ...mocks, './WebhookDeliveryHistory': { __esModule: true, default: History } });
  return renderToStaticMarkup(React.createElement(Config, { data, onUpdate() {} }));
}
test('current source shows fixed release, endpoint, stop and signature instructions', () => {
  const html = render();
  assert.match(html, /workflowWebhook\.statuses.enabled/);
  assert.match(html, /workflowWebhook\.disable/);
  assert.match(html, /workflowWebhook\.signing/);
  assert.match(html, /TGO-WEBHOOK-V1/);
  assert.match(html, /example.invalid\/api\/v1\/workflow-webhooks\/hook-a/);
  assert.doesNotMatch(html, /value="GET"|custom-path|path_suffix/);
});
test('a source can still be stopped when the execution environment is unavailable', () => {
  const html = render({ state: { webhook: { enabled: true }, execution_available: false } });
  const stop = html.match(/<button[^>]*>workflowWebhook.disable/);
  assert.ok(stop); assert.doesNotMatch(stop[0], /\sdisabled(?:=|[\s>])/);
  assert.match(html, /workflowWebhook.disabledCapability/);
});
test('ordinary staff and stale owners never render retained secrets', () => {
  const retained = { credential: { secret: 'synthetic-sensitive-credential' } };
  for (const html of [render(retained, 'user'), render({ ...retained, ownerKey: 'other' }), render({ ...retained, workflowId: 'flow-b' })]) {
    assert.doesNotMatch(html, /synthetic-sensitive|workflowWebhook.copyCredential|workflowWebhook.rotate/);
  }
});
test('an unknown request offers read and same-request retry instead of a second new operation', () => {
  const html = render({ pending: { operation: 'save', request: {} } });
  assert.match(html, /workflowWebhook.recover/); assert.match(html, /workflowWebhook.retry/);
  assert.match(html, /<button[^>]*disabled[^>]*>workflowWebhook.enable/);
});
test('legacy GET and custom paths require an explicit draft correction', () => {
  const html = render({}, 'admin', { type: 'webhook', method: 'GET', path: 'old' });
  assert.match(html, /workflowWebhook.legacy/); assert.match(html, /workflowWebhook.normalize/);
});
test('preparation availability never becomes execution availability', () => {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/workflowCapabilities.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports });
  const capabilities = { nodes: [{ type: 'webhook', available: false, preparation_available: true }] };
  assert.equal(exports.isWorkflowNodeConfigurable(capabilities, 'webhook'), true);
  assert.equal(exports.isWorkflowNodeAvailable(capabilities, 'webhook'), false);
  assert.equal(exports.isWorkflowNodeConfigurable(null, 'webhook'), false);
  assert.equal(exports.isWorkflowNodeConfigurable({ nodes: [{ type: 'webhook', preparation_available: 'true' }] }, 'webhook'), false);
});
