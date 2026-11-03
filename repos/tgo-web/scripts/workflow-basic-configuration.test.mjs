import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture({ dirty = true, saveError = null } = {}) {
  const events = [];
  const state = {
    currentWorkflow: { id: 'flow-a', tags: ['scenario:knowledge'], definition: { nodes: [
      { id: 'input', type: 'input', data: { label: '客户问题', input_variables: [{ name: 'question', type: 'string' }] } },
      { id: 'reply', type: 'agent', data: { label: '知识库答疑', agent_id: 'agent-a' } },
      { id: 'answer', type: 'answer', data: { label: '返回结果', output_type: 'variable', output_variable: 'reply.text' } },
    ], edges: [] } }, validationErrors: [], isDirty: dirty, isExecuting: false,
    updateNode() {}, setSelectedNode() {},
    saveWorkflow: async () => { events.push('save'); if (saveError) throw Error(saveError); state.isDirty = false; },
    setDebugPanelOpen: open => events.push(open ? 'open-test' : 'close-test'),
    startExecution: () => events.push('execute'),
  };
  const agentState = { agents: [{ id: 'agent-a', name: '商品客服', status: 'active', llmModel: '__system_default__', knowledgeBases: ['catalog'] }],
    refreshAgents() {}, agentsError: null, isLoadingAgents: false };
  const user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const react = {
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
    useEffect() {}, useRef: value => ({ current: value }), useState: initial => [initial, value => events.push(value)],
  };
  const exports = {};
  const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true };
  const utility = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/workflowSetup.ts', import.meta.url), 'utf8'), { compilerOptions }).outputText, { exports: utility });
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/business/WorkflowBasicConfiguration.tsx', import.meta.url), 'utf8'), { compilerOptions }).outputText, {
    exports, React: react, Error, require: name => {
      if (name === 'react') return react;
      if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key }) };
      if (name === 'lucide-react') return {};
      if (name === '@/stores/workflowStore') return { useWorkflowStore: Object.assign(() => state, { getState: () => state }) };
      if (name === '@/stores/aiStore') return { useAIStore: Object.assign(() => agentState, { getState: () => agentState }) };
      if (name === '@/stores/authStore') return { useAuthStore: Object.assign(() => ({ user }), { getState: () => ({ user }) }) };
      if (name === '@/utils/businessScenarios') return { linearWorkflowNodes: definition => definition.nodes, governedLogisticsWorkflowNodes: () => null };
      if (name === '@/utils/workflowSetup') return utility;
      if (name === './WorkflowAgentSelection') return { default: () => null };
      if (name === './WorkflowLogisticsBasicConfiguration') return { default: () => null };
      throw Error(`Unexpected dependency ${name}`);
    },
  });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child => Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  const button = flatten(exports.default({ onAdvanced() {} })).find(node => node?.type === 'button'
    && flatten(node).includes('businessScenarios.workflow.prepareTest'));
  return { button, events, state };
}

test('preparing a test saves edits before opening inputs and never starts execution', async () => {
  const h = fixture();
  assert.ok(h.button, 'configuration must have a visible test preparation action');
  await h.button.props.onClick();
  assert.ok(h.events.indexOf('save') < h.events.indexOf('open-test'));
  assert.ok(!h.events.includes('execute'));
});

test('a failed save keeps the configuration visible and reports the original error', async () => {
  const h = fixture({ saveError: 'HTTP 409: draft changed' });
  assert.ok(h.button);
  await h.button.props.onClick();
  assert.ok(!h.events.includes('open-test'));
  assert.ok(h.events.includes('HTTP 409: draft changed'));
  assert.equal(h.state.isDirty, true);
});

test('an unchanged draft opens the test inputs without writing or executing', async () => {
  const h = fixture({ dirty: false });
  assert.ok(h.button);
  await h.button.props.onClick();
  assert.ok(h.events.includes('open-test'));
  assert.ok(!h.events.includes('save'));
  assert.ok(!h.events.includes('execute'));
});
