import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function renderLogisticsBasic() {
  const changes = [];
  const nodes = Object.fromEntries([
    'input', 'order', 'order_check', 'logistics', 'logistics_check',
    'answer_success', 'answer_order_pending', 'answer_logistics_pending',
    'shipping_check', 'answer_not_shipped',
  ].map(name => [name, { id: `${name}-id`, type: ['order', 'logistics'].includes(name) ? 'business_query' : 'answer', data: {
    connection_id: 'old-connection', output_template: '原回复',
  } }]));
  const connection = { id: 'new-connection', name: '已核验连接', enabled: true,
    mode: 'http', test_status: 'passed', capabilities: ['order_query', 'logistics_query'] };
  const workflow = { currentWorkflow: { id: 'flow-a', definition: { nodes: Object.values(nodes) } },
    updateNode: (id, data) => changes.push(['node', id, data]),
    setNodes: updated => changes.push(['nodes', updated]),
    isExecuting: false, validationErrors: [] };
  const business = { connections: [connection], loading: false, error: null,
    hasMore: false, load() {} };
  const react = { createElement: (type, props, ...children) => ({ type, props: props || {}, children }), useEffect() {} };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/business/WorkflowLogisticsBasicConfiguration.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText, { exports, React: react, require: name => {
    if (name === 'react') return react;
    if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key }) };
    if (name === '@/stores/workflowStore') return { useWorkflowStore: Object.assign(() => workflow, { getState: () => workflow }) };
    if (name === '@/stores/businessConnectionStore') return { useBusinessConnectionStore: Object.assign(() => business, { getState: () => business }) };
    throw Error(`Unexpected dependency ${name}`);
  } });
  const flatten = node => [node, ...(node?.children || []).flatMap(child =>
    Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  return { tree: flatten(exports.default({ nodes, onAdvanced() {} })), changes };
}

test('basic order form keeps both governed queries on the same connection', () => {
  const { tree, changes } = renderLogisticsBasic();
  const select = tree.find(node => node?.type === 'select');
  assert.ok(select);
  select.props.onChange({ target: { value: 'new-connection' } });
  assert.equal(changes.length, 1, 'both query nodes should change in one store update');
  assert.equal(changes[0][0], 'nodes');
  const updated = changes[0][1];
  assert.equal(updated.find(node => node.id === 'order-id').data.connection_id, 'new-connection');
  assert.equal(updated.find(node => node.id === 'logistics-id').data.connection_id, 'new-connection');
  assert.equal(updated.find(node => node.id === 'answer_success-id').data.connection_id, 'old-connection');
  assert.equal(tree.some(node => node === 'businessScenarios.workflow.prepareTest'
    || node?.type === 'button' && node.props.onClick?.toString().includes('startExecution')), false);
});

test('basic order form edits an answer without changing query or branch nodes', () => {
  const { tree, changes } = renderLogisticsBasic();
  const answer = tree.find(node => node?.type === 'textarea');
  assert.ok(answer);
  answer.props.onChange({ target: { value: '请稍后核对物流状态。' } });
  assert.deepEqual(JSON.parse(JSON.stringify(changes)), [['node', 'answer_success-id', { output_template: '请稍后核对物流状态。' }]]);
});
