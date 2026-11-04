import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function load(path) {
  const context = { exports: {}, URL, crypto: { randomUUID: (() => { let n = 0; return () => `id-${++n}`; })() } };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  return context.exports;
}

test('workflow palette uses server capabilities and refuses missing or unsupported nodes', () => {
  const { isWorkflowNodeAvailable } = load('../src/utils/workflowCapabilities.ts');
  const capability = { nodes: [{ type: 'tool', available: true }, { type: 'timer', available: false }] };
  assert.equal(isWorkflowNodeAvailable(null, 'tool'), false);
  assert.equal(isWorkflowNodeAvailable(capability, 'tool'), true);
  assert.equal(isWorkflowNodeAvailable(capability, 'timer'), false);
  assert.equal(isWorkflowNodeAvailable(capability, 'unknown'), false);
  assert.equal(isWorkflowNodeAvailable({ nodes: [{ type: 'tool', available: 'true' }] }, 'tool'), false);
});

test('templates create isolated drafts with complete reference mappings', () => {
  const { buildScenarioWorkflow } = load('../src/utils/businessScenarios.ts');
  const first = buildScenarioWorkflow('knowledge', { name: '咨询', agentId: 'agent-a' });
  const second = buildScenarioWorkflow('knowledge', { name: '咨询', agentId: 'agent-a' });
  assert.notEqual(first.nodes[0].id, second.nodes[0].id);
  assert.equal(first.nodes[1].data.agent_id, 'agent-a');
  assert.equal(first.nodes[1].data.input_mapping.message, '{{input.question}}');
  assert.equal(first.nodes[2].data.output_variable, 'reply.text');
  assert.equal(first.edges.length, 2);
  assert.equal(first.status, undefined, 'template cannot activate itself');
});

test('order workflow uses only governed connection nodes and safe branches', () => {
  const { buildScenarioWorkflow, BUSINESS_SCENARIOS } = load('../src/utils/businessScenarios.ts');
  assert.equal(BUSINESS_SCENARIOS.find(scene => scene.module === 'workflows' && scene.id === 'logistics').available, true);
  const connectionId = '71d90c63-42be-440f-98d0-f34387c0e964';
  const draft = buildScenarioWorkflow('logistics', { name: '物流', agentId: 'agent-a', connectionId });
  const orderInput = draft.nodes.find(node => node.type === 'input').data.input_variables[0];
  assert.equal(orderInput.min_length, 4);
  assert.equal(orderInput.max_length, 64);
  assert.equal(draft.nodes.filter(node => node.type === 'business_query').length, 2);
  assert.deepEqual(Array.from(draft.nodes.filter(node => node.type === 'business_query'), node => node.data.operation), ['order_query', 'logistics_query']);
  assert.ok(draft.nodes.filter(node => node.type === 'business_query').every(node => node.data.connection_id === connectionId));
  assert.ok(draft.nodes.every(node => !['api', 'tool', 'agent', 'llm'].includes(node.type)));
  assert.ok(draft.edges.some(edge => edge.sourceHandle === 'false'));
  assert.ok(draft.nodes.some(node => node.type === 'condition' && node.data.variable === 'logistics.error_code' && node.data.compare_value === 'not_shipped'));
  assert.ok(!JSON.stringify(draft).includes('apiUrl'));
  assert.ok(!JSON.stringify(draft).includes('input.order_no}}'));
});

test('basic order editor recognizes only its complete governed graph', () => {
  const { buildScenarioWorkflow, governedLogisticsWorkflowNodes } = load('../src/utils/businessScenarios.ts');
  const connectionId = '71d90c63-42be-440f-98d0-f34387c0e964';
  const draft = buildScenarioWorkflow('logistics', { name: '物流', agentId: 'agent-a', connectionId });
  const definition = { nodes: [...draft.nodes].reverse(), edges: [...draft.edges].reverse() };
  const recognized = governedLogisticsWorkflowNodes(definition);
  assert.equal(recognized.order.data.connection_id, connectionId);
  assert.equal(recognized.logistics.data.connection_id, connectionId);
  assert.equal(recognized.answer_success.type, 'answer');
  assert.equal(governedLogisticsWorkflowNodes({ ...definition, edges: draft.edges.slice(1) }), null);
  const changedCondition = structuredClone(definition);
  changedCondition.nodes.find(node => node.data.reference_key === 'order_check').data.variable = 'input.order_no';
  assert.equal(governedLogisticsWorkflowNodes(changedCondition), null);
  const differentConnections = structuredClone(definition);
  differentConnections.nodes.find(node => node.data.reference_key === 'logistics').data.connection_id = 'other';
  assert.equal(governedLogisticsWorkflowNodes(differentConnections), null);
  const changedBranch = structuredClone(definition);
  changedBranch.edges.find(edge => edge.sourceHandle === 'false').sourceHandle = 'true';
  assert.equal(governedLogisticsWorkflowNodes(changedBranch), null);
});

test('missing dependencies cannot produce an executable template', () => {
  const { buildScenarioWorkflow } = load('../src/utils/businessScenarios.ts');
  assert.throws(() => buildScenarioWorkflow('knowledge', { name: 'x' }));
  assert.throws(() => buildScenarioWorkflow('logistics', { name: 'x' }));
  assert.throws(() => buildScenarioWorkflow('logistics', { name: 'x', agentId: 'a', connectionId: 'https://foreign.example.test' }));
  assert.throws(() => buildScenarioWorkflow('refund', { name: 'x', agentId: 'a' }));
});

test('device tools are literal capabilities; shell never implies screen control', () => {
  const { summarizeDeviceTools } = load('../src/utils/businessScenarios.ts');
  const summary = summarizeDeviceTools([{ name: 'fs_read' }, { name: 'shell_exec' }]);
  assert.equal(summary.files, true);
  assert.equal(summary.commands, true);
  assert.equal(summary.screen, false);
  assert.equal(summarizeDeviceTools([{ name: 'take_screenshot' }]).screen, false);
  assert.equal(summarizeDeviceTools([{ name: 'screenshot' }, { name: 'click' }, { name: 'type_text' }]).screen, true);
});

test('basic form preserves graph order and refuses advanced or disconnected definitions', () => {
  const { buildScenarioWorkflow, linearWorkflowNodes } = load('../src/utils/businessScenarios.ts');
  const draft = buildScenarioWorkflow('knowledge', { name: 'x', agentId: 'a' });
  const shuffled = { nodes: [...draft.nodes].reverse(), edges: draft.edges };
  assert.equal(linearWorkflowNodes(shuffled)[0].type, 'input');
  assert.equal(linearWorkflowNodes(shuffled)[2].type, 'answer');
  assert.equal(linearWorkflowNodes({ ...shuffled, edges: [] }), null);
  assert.equal(linearWorkflowNodes({ ...shuffled, edges: [...shuffled.edges, { source: draft.nodes[0].id, target: draft.nodes[2].id }] }), null);
  assert.equal(linearWorkflowNodes({ ...shuffled, nodes: [...shuffled.nodes, { id: 'advanced', type: 'condition' }] }), null);
});
