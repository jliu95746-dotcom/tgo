import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture({ saveError = null, executionOutput = null, executionStatus = 'completed', executionFailure = null, eventWorkflow = false, historyView = false } = {}) {
  const events = [];
  const user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const state = { currentWorkflow: { id: 'flow-a', tags: ['scenario:knowledge'], definition: { nodes: [
    { id: 'input', type: 'input', data: { input_variables: [{ name: 'question', type: 'string', description: '客户咨询内容', required: true }] } },
  ] } }, isDirty: true, isExecuting: false, nodeExecutionMap: {}, currentExecution: executionOutput === null ? null : { status: executionStatus, output: executionOutput, error: executionFailure }, executionError: null,
    executionPanelMode: historyView ? 'history' : 'debug',
    debugInput: { question: '客户自己的问题', customer_type: '会员' }, setDebugInput: value => events.push(value),
    saveWorkflow: async () => { events.push('save'); if (saveError) throw Error(saveError); state.isDirty = false; },
    startExecution: input => events.push({ executed: input }) };
  if (eventWorkflow) state.currentWorkflow.definition.nodes = [{ id: 'event', type: 'event', data: { event_type: 'conversation.archived' } }];
  const react = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }), useMemo: fn => fn(),
    useId: () => 'input-id', useRef: value => ({ current: value }), useState: initial => [initial, value => events.push(value)] };
  const exports = {};
  const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true };
  const inputs = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/workflowInputs.ts', import.meta.url), 'utf8'), { compilerOptions }).outputText, { exports: inputs });
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/workflow/panels/DebugPanel.tsx', import.meta.url), 'utf8'), { compilerOptions }).outputText, {
    exports, Error, React: react, require: name => {
      if (name === 'react') return react;
      if (name === 'react-i18next') return { useTranslation: () => ({ t: (key, fallback) => typeof fallback === 'string' ? fallback : key }) };
      if (name === 'lucide-react') return {};
      if (name === 'react-markdown') return function ReactMarkdown() {};
      if (name === '@/stores/workflowStore') return { useWorkflowStore: Object.assign(() => state, { getState: () => state }) };
      if (name === '@/stores/authStore') return { useAuthStore: { getState: () => ({ user }) } };
      if (name === '@/utils/workflowInputs') return inputs;
      if (name === './EventTestPanel') return function EventTestPanel() {};
      throw Error(`Unexpected dependency ${name}`);
    },
  });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child => Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  const tree = flatten(exports.default());
  const button = label => tree.find(node => node?.type === 'button' && flatten(node).includes(label));
  return { events, state, run: button('运行工作流'), example: button('businessScenarios.workflow.exampleQuestion'), tree };
}

test('running after configuration edits saves first and sends the original business field names', async () => {
  const h = fixture();
  await h.run.props.onClick();
  const executed = h.events.find(event => event?.executed);
  assert.ok(h.events.includes('save'));
  assert.ok(h.events.indexOf('save') < h.events.indexOf(executed));
  assert.equal(executed.executed.question, '客户自己的问题');
  assert.equal(executed.executed.customer_type, '会员');
});

test('save failure prevents execution and reports the failure', async () => {
  const h = fixture({ saveError: 'HTTP 409: current draft changed' });
  await h.run.props.onClick();
  assert.ok(!h.events.some(event => event?.executed));
  assert.ok(h.events.includes('HTTP 409: current draft changed'));
});

test('explicitly using a question example keeps other input fields and does not run', () => {
  const h = fixture();
  h.example.props.onClick();
  const input = h.events.find(event => event?.question);
  assert.equal(input.customer_type, '会员');
  assert.ok(!h.events.includes('save'));
  assert.ok(!h.events.some(event => event?.executed));
});

test('a text answer is rendered as readable Markdown without JSON escapes', () => {
  const answer = '**结论：不建议。**\n\n建议选择双肩包。';
  const h = fixture({ executionOutput: answer });
  const markdown = h.tree.find(node => node?.type?.name === 'ReactMarkdown');
  assert.ok(markdown?.children.includes(answer));
  assert.ok(!h.tree.some(node => node?.type === 'pre' && node.children.includes(JSON.stringify(answer))));
});

test('a failed workflow shows its error without presenting an empty successful final output', () => {
  const h = fixture({ executionOutput: {}, executionStatus: 'failed', executionFailure: '知识库查询失败' });
  assert.ok(h.tree.some(node => node?.children?.includes('知识库查询失败')));
  assert.ok(!h.tree.some(node => node?.children?.includes('最终输出')));
});

test('event workflows open the dedicated published-event panel instead of the ordinary execute button', () => {
  const h = fixture({ eventWorkflow: true });
  const panel = h.tree.find(node => node?.type?.name === 'EventTestPanel');
  assert.equal(panel?.props.workflowId, 'flow-a');
  assert.equal(h.run, undefined);
});

test('history viewing shows the persisted result without test inputs, run or debug notes', () => {
  const h = fixture({ historyView: true, executionOutput: '持久运行结果' });
  assert.equal(h.run, undefined);
  assert.ok(!h.tree.some(node => ['input', 'textarea', 'select'].includes(node?.type)));
  assert.ok(h.tree.some(node => node?.children?.includes('workflow.debug.historyTitle')));
  assert.ok(h.tree.some(node => node?.children?.includes('workflow.debug.historyNote')));
  assert.ok(!h.tree.some(node => node?.children?.includes('businessScenarios.workflow.realTest')));
  assert.ok(!h.tree.some(node => node?.children?.includes('workflow.debug.persistence_note')));
  assert.ok(h.tree.some(node => node?.type?.name === 'ReactMarkdown' && node.children.includes('持久运行结果')));
});

test('a historical event result is not replaced by the event test panel', () => {
  const h = fixture({ historyView: true, eventWorkflow: true, executionOutput: '事件运行结果' });
  assert.ok(!h.tree.some(node => node?.type?.name === 'EventTestPanel'));
  assert.ok(h.tree.some(node => node?.type?.name === 'ReactMarkdown' && node.children.includes('事件运行结果')));
});

test('a handler captured before switching to history cannot save or start a new test', async () => {
  const h = fixture();
  h.state.executionPanelMode = 'history';
  await h.run.props.onClick();
  assert.ok(!h.events.includes('save'));
  assert.ok(!h.events.some(event => event?.executed));
});
