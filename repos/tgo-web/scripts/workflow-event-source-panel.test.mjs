import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

const context = { workflow_id: 'flow-a', event_type: 'staff.session.closed', release_id: 'release-a', release_digest: 'digest-a', version: 3, status: 'active' };
const subscription = { workflow_id: 'flow-a', event_type: 'staff.session.closed', enabled: true, revision: 2, release_id: 'release-a', release_digest: 'digest-a', version: 3 };
function component(file, mocks = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, { exports, require: name => {
    if (name in mocks) return mocks[name];
    if (name === 'react') return React;
    if (name === 'react/jsx-runtime') return jsxRuntime;
    if (name === 'react-i18next') return { useTranslation: () => ({ t: (key, values) => key + (values?.version ? ` v${values.version}` : '') }) };
    if (name === 'lucide-react') return new Proxy({}, { get: () => () => null });
    throw Error(`Unexpected dependency: ${name}`);
  } });
  return exports.default;
}

function render(patch = {}, role = 'admin') {
  const state = { ownerKey: 'project-a:staff-a:admin', workflowId: 'flow-a', subscription, context, deliveries: [], loading: false, saving: false, error: '', contextError: '', deliveryError: '', ...patch };
  const user = { id: 'staff-a', project_id: 'project-a', role };
  const Card = component('components/workflow/panels/EventSourceConfiguration.tsx', {
    '@/stores/authStore': { useAuthStore: selector => selector({ user }) },
    '@/stores/workflowStore': { useWorkflowStore: selector => selector({ currentWorkflow: { id: 'flow-a', publication_revision: 1, status: 'active' } }) },
    '@/stores/workflowEventSubscriptionStore': { useWorkflowEventSubscriptionStore: () => state },
    '@/stores/workflowEventTestStore': { workflowEventOwner: value => value?.role === 'admin' ? `${value.project_id}:${value.id}:admin` : null },
  });
  return renderToStaticMarkup(React.createElement(Card, { workflowId: 'flow-a' }));
}

test('enabled source shows bound version, scope and stop semantics', () => {
  const html = render();
  assert.match(html, /subscription.enabled/); assert.match(html, /subscription.scope/);
  assert.match(html, /subscription.stopHint/); assert.match(html, /subscription.disable/);
});

test('missing publication still renders an enabled close button', () => {
  const html = render({ context: null, contextError: 'Workflow disabled' });
  const closeButton = html.match(/<button\b[^>]*>workflowEvents.subscription.disable/);
  assert.ok(closeButton);
  assert.doesNotMatch(closeButton[0], /\sdisabled(?:=|[\s>])/);
  assert.match(html, /Workflow disabled/);
});

test('changed release exposes explicit rebind and never reports active current binding', () => {
  const html = render({ subscription: { ...subscription, release_id: 'old' } });
  assert.match(html, /subscription.releaseChanged/); assert.match(html, /subscription.reenable/);
  assert.doesNotMatch(html, />workflowEvents.subscription.enabled</);
});

test('manual event names expose no enabled automation action', () => {
  const html = render({ context: { ...context, event_type: 'custom.event' }, subscription: { ...subscription, enabled: false } });
  assert.match(html, /subscription.selectSource/); assert.doesNotMatch(html, />workflowEvents.subscription.enable</);
});

test('delivery failures and accepted execution identifiers are visible', () => {
  const html = render({ deliveries: [{ id: 'delivery-a', session_id: 'session-a', status: 'failed', attempts: 3, last_error: 'subscription_changed', execution_id: 'run-a', created_at: '2026-10-23T10:00:00Z' }] });
  assert.match(html, /subscription.errors.subscription_changed/); assert.match(html, /run-a/); assert.match(html, /session-a/);
  assert.match(html, /subscription.deliveryStatus.failed/);
});

test('retained records are hidden immediately for another owner or workflow and non-admin', () => {
  for (const html of [render({ ownerKey: 'other-owner' }), render({ workflowId: 'flow-b' }), render({}, 'agent')]) {
    assert.doesNotMatch(html, /subscription.disable|subscription.enabled|v3/);
  }
});

test('source selection locks known type and keeps arbitrary events manual', () => {
  const Config = component('components/workflow/panels/EventNodeConfig.tsx', {
    './SessionClosedTemplateAction': { default: () => null, __esModule: true },
  });
  const auto = renderToStaticMarkup(React.createElement(Config, { data: { type: 'event', event_type: 'staff.session.closed' }, onUpdate() {} }));
  const manual = renderToStaticMarkup(React.createElement(Config, { data: { type: 'event', event_type: 'custom.event' }, onUpdate() {} }));
  assert.match(auto, /value="staff.session.closed" selected/); assert.doesNotMatch(auto, /<input/);
  assert.match(manual, /value="manual" selected/); assert.match(manual, /value="custom.event"/);
});
