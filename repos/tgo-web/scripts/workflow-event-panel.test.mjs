import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

const context = { workflow_id: 'flow-a', event_type: 'conversation.archived', release_id: 'release-a', release_digest: 'digest-a', version: 3, status: 'active' };
const attempt = { request_id: 'request-a', event_type: context.event_type, expected_release_id: context.release_id, expected_release_digest: context.release_digest, data: { text: '原始数据' } };
const translate = (key, values) => key + (values?.version ? ` v${values.version}` : '');

function render({ role = 'admin', retainedOwner = 'project-a:staff-a:admin', activeWorkflowId = 'flow-a', session = {} } = {}) {
  const snapshot = { ownerKey: retainedOwner, activeWorkflowId, sessions: { 'flow-a': {
    context, jsonInput: '{"text":"原始数据"}', attempt: null, accepted: null, execution: null,
    loadingContext: false, sending: false, refreshing: false, cancelling: false, rejected: false, error: '', ...session,
  } } };
  const user = { id: 'staff-a', project_id: 'project-a', role };
  const ownerOf = value => value?.role === 'admin' ? `${value.project_id}:${value.id}:admin` : null;
  const exports = {};
  const source = readFileSync(new URL('../src/components/workflow/panels/EventTestPanel.tsx', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, {
    exports, require: name => {
      if (name === 'react') return React;
      if (name === 'react/jsx-runtime') return jsxRuntime;
      if (name === 'react-i18next') return { useTranslation: () => ({ t: translate }) };
      if (name === 'react-markdown') return props => React.createElement('article', { 'data-markdown': 'true' }, props.children);
      if (name === 'lucide-react') return new Proxy({}, { get: () => () => null });
      if (name === '@/stores/authStore') return { useAuthStore: selector => selector({ user }) };
      if (name === '@/stores/workflowEventTestStore') return { useWorkflowEventTestStore: () => snapshot, workflowEventOwner: ownerOf };
      if (name === '@/stores/workflowEventTestActions') return { eventTestTerminal: status => ['completed', 'failed', 'cancelled', 'rejected', 'expired'].includes(status) };
      if (name === './EventSourceConfiguration') return { default: () => null, __esModule: true };
      throw Error(`Unexpected dependency ${name}`);
    },
  });
  return renderToStaticMarkup(React.createElement(exports.default, { workflowId: 'flow-a', onClose() {} }));
}

test('administrator sees published version, real execution notice and event JSON fields', () => {
  const html = render();
  assert.match(html, /workflowEvents.publishedHint v3/);
  assert.match(html, /conversation.archived/);
  assert.match(html, /workflowEvents.dataLabel/);
  assert.match(html, /workflowEvents.send/);
});

test('ordinary staff and a switched owner cannot view retained event data', () => {
  const staffHtml = render({ role: 'agent' });
  assert.match(staffHtml, /workflowEvents.adminOnly/);
  assert.doesNotMatch(staffHtml, /原始数据|conversation.archived|<textarea/);
  const switchedHtml = render({ retainedOwner: 'another-project:staff-a:admin' });
  assert.doesNotMatch(switchedHtml, /原始数据|conversation.archived/);
  const switchedWorkflow = render({ activeWorkflowId: 'flow-b' });
  assert.doesNotMatch(switchedWorkflow, /原始数据|conversation.archived/);
});

test('unknown submission exposes only same-event retry and disables reset', () => {
  const html = render({ session: { attempt, error: 'Network error' } });
  assert.match(html, /workflowEvents.retry/);
  assert.match(html, /workflowEvents.uncertain/);
  assert.match(html, /<textarea[^>]*disabled=""/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>workflowEvents.reset/);
});

test('a completed run shows its real output as Markdown and no cancellation action', () => {
  const html = render({ session: { attempt, accepted: { execution_id: 'run-a', status: 'pending', deduplicated: true },
    execution: { id: 'run-a', status: 'completed', node_executions: [], output: '**摘要**\n已整理' } } });
  assert.match(html, /workflowEvents.deduplicated/);
  assert.match(html, /data-markdown="true"/);
  assert.match(html, /workflowEvents.finalOutput/);
  assert.doesNotMatch(html, />workflowEvents.cancel</);
});

test('event API uses the administrator paths and forwards the fixed request without internal secrets', async () => {
  const calls = [], exports = {};
  const api = { get: async (...args) => { calls.push(['GET', ...args]); return context; },
    post: async (...args) => { calls.push(['POST', ...args]); return { execution_id: 'run-a', status: 'pending', deduplicated: false }; } };
  const source = readFileSync(new URL('../src/services/workflowApi.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, { exports, URLSearchParams, require: name => {
    if (name === './api') return api;
    if (name === '@/i18n') return { t: translate };
    throw Error(`Unexpected dependency ${name}`);
  } });
  const signal = new AbortController().signal;
  await exports.WorkflowApiService.getEventTestContext('flow-a', signal);
  await exports.WorkflowApiService.sendEventTest('flow-a', attempt, signal);
  assert.equal(calls[0][1], '/v1/ai/workflows/flow-a/event-test-context');
  assert.equal(calls[1][1], '/v1/ai/workflows/flow-a/event-tests');
  assert.equal(calls[1][2], attempt);
  assert.deepEqual(Object.keys(calls[1][3]), ['signal']);
  assert.equal(calls[1][3].signal, signal);
});
