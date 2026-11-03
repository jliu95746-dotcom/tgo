import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture(kind) {
  let resolveRun;
  const pending = new Promise(resolve => { resolveRun = resolve; });
  const state = { currentWorkflow: { id: 'flow-a' }, isExecuting: false,
    executionPanelMode: 'debug', executionError: 'previous error', currentExecution: null };
  const auth = { user: { id: 'owner-a' } };
  const row = { id: 'delivery-a', execution_id: 'run-a', release_id: 'release-a',
    status: 'accepted', created_at: '2026-10-25T12:00:00Z', attempts: 1, binding_revision: 1 };
  const source = { workflowId: 'flow-a', ownerKey: 'owner-a', state: null,
    history: { items: [row], offset: 0, limit: 20, total: 1, has_more: false } };
  const run = { id: 'run-a', workflow_id: 'flow-a', release_id: 'release-a',
    source_channel: 'external_webhook', source_message_id: 'webhook-delivery-a', node_executions: [] };
  const react = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
    useState: initial => [initial, () => {}] };
  const workflow = Object.assign(selector => selector(state), {
    getState: () => state, setState: patch => Object.assign(state, patch),
  });
  const mocks = {
    react,
    'react-i18next': { useTranslation: () => ({ t: key => key, i18n: { language: 'zh-CN' } }) },
    'lucide-react': {},
    '@/stores/workflowStore': { useWorkflowStore: workflow },
    '@/stores/authStore': { useAuthStore: { getState: () => auth } },
    '@/stores/workflowEventTestStore': { workflowEventOwner: user => user.id },
    '@/stores/workflowWebhookStore': { useWorkflowWebhookStore: () => source },
    '@/stores/workflowScheduleStore': { useWorkflowScheduleStore: () => source },
    '@/services/workflowApi': { WorkflowApiService: { getExecution: () => pending } },
    './WorkflowScheduleConfiguration': { scheduleTime: value => value },
  };
  const file = kind === 'webhook' ? 'WebhookDeliveryHistory' : 'WorkflowScheduleConfiguration';
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/components/workflow/panels/${file}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText, { exports, React: react, require: name => {
    if (name in mocks) return mocks[name];
    throw Error(`Unexpected dependency ${name}`);
  } });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child => Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  const tree = flatten(exports.default());
  const label = kind === 'webhook' ? 'workflowWebhook.inspect' : 'workflowSchedule.inspect';
  const button = tree.find(node => node?.type === 'button' && flatten(node).includes(label));
  assert.ok(button);
  return { state, auth, run, resolveRun, open: () => button.props.onClick() };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
for (const kind of ['webhook', 'schedule']) {
  test(`${kind} history opens the selected persisted record in read-only mode`, async () => {
    const h = fixture(kind);
    h.open(); h.resolveRun(h.run); await settle();
    assert.equal(h.state.currentExecution.id, 'run-a');
    assert.equal(h.state.executionPanelMode, 'history');
    assert.equal(h.state.executionError, null);
  });
  test(`${kind} history cannot replace a debug run started while the record was loading`, async () => {
    const h = fixture(kind);
    h.open(); h.state.isExecuting = true;
    h.resolveRun(h.run); await settle();
    assert.equal(h.state.currentExecution, null);
    assert.equal(h.state.executionPanelMode, 'debug');
  });
  test(`${kind} history rejects a response after the account changed`, async () => {
    const h = fixture(kind);
    h.open(); h.auth.user.id = 'other-owner';
    h.resolveRun(h.run); await settle();
    assert.equal(h.state.currentExecution, null);
  });
  test(`${kind} history rejects a response for a different execution`, async () => {
    const h = fixture(kind);
    h.open(); h.resolveRun({ ...h.run, id: 'different-run' }); await settle();
    assert.equal(h.state.currentExecution, null);
  });
}
