import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function load() {
  const context = { exports: {} };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/workflowSetup.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  return context.exports;
}

const agent = (id, changes = {}) => ({ id, status: 'active', llmModel: '__system_default__', knowledgeBases: ['catalog'], ...changes });

test('only a single enabled employee with a model and linked knowledge is prefilled', () => {
  const { selectWorkflowAgent } = load();
  assert.equal(selectWorkflowAgent('', false, [agent('a')]), 'a');
  assert.equal(selectWorkflowAgent('', false, [agent('a'), agent('b')]), '');
  for (const changes of [{ status: 'inactive' }, { llmModel: '' }, { knowledgeBases: [] }]) {
    assert.equal(selectWorkflowAgent('', false, [agent('a', changes)]), '');
  }
  assert.equal(selectWorkflowAgent('', false, [agent('inactive', { status: 'inactive' }), agent('a')]), 'a');
});

test('refreshing recommendations preserves a manual choice or an explicitly cleared selection', () => {
  const { selectWorkflowAgent } = load();
  assert.equal(selectWorkflowAgent('chosen', true, [agent('other')]), 'chosen');
  assert.equal(selectWorkflowAgent('', true, [agent('other')]), '');
  assert.equal(selectWorkflowAgent('removed', false, [agent('other')]), 'removed');
});

test('business labels resolve exact input, employee answer and query body references', () => {
  const { describeWorkflowReference } = load();
  const nodes = [
    { id: 'in', type: 'input', data: { reference_key: 'input', input_variables: [{ name: 'question', description: '客户咨询内容' }] } },
    { id: 'agent', type: 'agent', data: { reference_key: 'reply', label: '知识库答疑' } },
    { id: 'query', type: 'api', data: { reference_key: 'orders', label: '查询订单与物流' } },
  ];
  assert.equal(JSON.stringify(describeWorkflowReference('{{ input.question }}', nodes)), JSON.stringify({ kind: 'input', label: '客户咨询内容' }));
  assert.equal(JSON.stringify(describeWorkflowReference('reply.text', nodes)), JSON.stringify({ kind: 'agent_answer', label: '知识库答疑' }));
  assert.equal(JSON.stringify(describeWorkflowReference('orders.body', nodes)), JSON.stringify({ kind: 'api_result', label: '查询订单与物流' }));
  assert.equal(describeWorkflowReference('{{input.missing}}', nodes), null);
  assert.equal(describeWorkflowReference('orders.body.status', nodes), null);
  assert.equal(describeWorkflowReference('prefix {{input.question}}', nodes), null);
  assert.equal(describeWorkflowReference('reply.unknown', nodes), null);
});

test('ambiguous reference keys are not presented as a known business result', () => {
  const { describeWorkflowReference } = load();
  const nodes = ['first', 'second'].map(id => ({ id, type: 'agent', data: { reference_key: 'reply', label: id } }));
  assert.equal(describeWorkflowReference('reply.text', nodes), null);
});
