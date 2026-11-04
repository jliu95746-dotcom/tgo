import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture(role = 'admin') {
  let user = { id: 'staff-a', project_id: 'project-a', role };
  const state = { currentWorkflow: { id: 'flow-a', definition: { nodes: [], edges: [] }, status: 'active', version: 2, publication_revision: 1, published_version: 1 }, isDirty: false, history: [], historyIndex: -1,
    saveWorkflow: async () => {}, validate: async () => true, validationErrors: [], loadWorkflows() {}, setDebugPanelOpen() {}, undo() {}, redo() {} };
  let resolvePublication;
  let writes = 0;
  const store = Object.assign(() => state, { getState: () => state, setState: value => { writes++; Object.assign(state, typeof value === 'function' ? value(state) : value); } });
  const auth = Object.assign(selector => selector({ user }), { getState: () => ({ user }) });
  const react = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }), useState: initial => [initial, () => {}] };
  const exports = {};
  const source = readFileSync(new URL('../src/components/workflow/WorkflowToolbar.tsx', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true } }).outputText, {
    exports, require: name => {
      if (name === 'react') return react;
      if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key }) };
      if (name === 'lucide-react') return {};
      if (name === '@/stores/workflowStore') return { useWorkflowStore: store };
      if (name === '@/stores/authStore') return { useAuthStore: auth };
      if (name === '@/hooks/useToast') return { useToast: () => ({ showToast() {} }) };
      if (name === '@/services/workflowApi') return { WorkflowApiService: { publishWorkflow: () => new Promise(resolve => { resolvePublication = resolve; }) } };
      if (name === './WorkflowReleases') return { default: () => null };
      throw Error(`Unexpected dependency ${name}`);
    },
  });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child => Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  const button = flatten(exports.default()).find(node => node?.type === 'button' && node.props.title === 'businessScenarios.workflow.publishHelp');
  return { button, state, writes: () => writes, changeUser: next => { user = next; }, finish: result => resolvePublication(result) };
}

test('an administrator can publish an already saved new draft of an active workflow', () => {
  assert.equal(Boolean(fixture().button.props.disabled), false);
});

test('publication is unavailable to ordinary staff', () => {
  assert.equal(Boolean(fixture('user').button.props.disabled), true);
});

test('an old publication response cannot change the new staff session or overwrite draft edits', async () => {
  const h = fixture();
  const pending = h.button.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  h.state.currentWorkflow = { ...h.state.currentWorkflow, name: '未保存的新名称' };
  h.changeUser({ id: 'staff-b', project_id: 'project-b', role: 'admin' });
  h.finish({ id: 'flow-a', status: 'active', published_version: 2, publication_revision: 2 });
  await pending;
  assert.equal(h.writes(), 0);
  assert.equal(h.state.currentWorkflow.published_version, 1);
  assert.equal(h.state.currentWorkflow.name, '未保存的新名称');
});

test('a publication response updates only the production pointer and keeps new unsaved edits', async () => {
  const h = fixture();
  const pending = h.button.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  h.state.currentWorkflow = { ...h.state.currentWorkflow, name: '未保存的新名称', version: 3 };
  h.finish({ id: 'flow-a', name: '旧名称', version: 2, status: 'active', published_version: 2, publication_revision: 2 });
  await pending;
  assert.equal(h.state.currentWorkflow.name, '未保存的新名称');
  assert.equal(h.state.currentWorkflow.version, 3);
  assert.equal(h.state.currentWorkflow.published_version, 2);
});

test('external trigger publication delegates full preparation validation to publish API', async () => {
  const h = fixture();
  h.state.currentWorkflow.definition.nodes = [{ id: 'source', type: 'webhook' }];
  h.state.validate = async () => { throw Error('ordinary execution validation must not block preparation'); };
  const pending = h.button.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  h.finish({ id: 'flow-a', status: 'active', published_version: 2, publication_revision: 2 });
  await pending;
  assert.equal(h.state.currentWorkflow.published_version, 2);
});
