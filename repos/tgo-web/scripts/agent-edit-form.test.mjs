import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/ai/EditAgentModal.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('EditAgentModal.tsx', source, ts.ScriptTarget.Latest, true);
function evaluate(expression, scope) {
  const context = { Error, console: { error() {} }, ...scope };
  vm.runInNewContext(ts.transpileModule(`globalThis.result = ${expression}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  return context.result;
}
function find(predicate) {
  let result;
  function visit(node) {
    if (!result && predicate(node)) result = node;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(result);
  return result;
}
function callback(name, scope) {
  return evaluate(find(node => ts.isVariableDeclaration(node) && node.name.getText(tree) === name).initializer.getText(tree), scope);
}
const t = (_key, fallback) => fallback;
const saved = {
  id: 'agent-a', name: '价格咨询专员', role: '商品咨询', description: '按商品数据回答',
  llmModel: 'provider-a:chat:v1', tools: ['tool-a', 'tool-b'],
  toolConfigs: { 'tool-a': { region: 'cn' } }, knowledgeBases: ['kb-a'],
  workflows: ['workflow-a'], boundDeviceId: 'device-a', skills_enabled: false,
  humanization_skill_name: 'brand-style', humanization_skill_enabled: true,
  config: { markdown: false, add_datetime_to_context: false, tool_call_limit: 3, num_history_runs: 0 },
};

test('reset restores string tool IDs and all saved resource bindings', () => {
  let result;
  callback('handleReset', { agent: saved, isLoadingAgent: false, t, reset: value => { result = value; } })();
  assert.deepEqual(Array.from(result.tools), saved.tools);
  assert.equal(result.toolConfigs, saved.toolConfigs);
  assert.deepEqual(Array.from(result.knowledgeBases), ['kb-a']);
  assert.deepEqual(Array.from(result.workflows), ['workflow-a']);
  assert.equal(result.boundDeviceId, 'device-a');
  assert.equal(result.llmModel, 'provider-a:chat:v1');
  assert.equal(result.skills_enabled, false);
  assert.equal(result.humanization_skill_name, 'brand-style');
  assert.equal(result.humanization_skill_enabled, true);
  assert.equal(result.markdown, false);
  assert.equal(result.num_history_runs, 0);
});

test('reset does not invent a model when the saved employee has none', () => {
  let result;
  callback('handleReset', { agent: { ...saved, llmModel: '' }, isLoadingAgent: false,
    t, reset: value => { result = value; } })();
  assert.equal(result.llmModel, '');
});

test('initializing an unconfigured employee also leaves the model empty', () => {
  let result;
  const effect = find(node => ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect'
    && node.arguments[0].getText(tree).includes('initializedAgent.current !== agent'));
  evaluate(effect.arguments[0].getText(tree), {
    agent: { ...saved, llmModel: '' }, initializedAgent: { current: null }, isLoadingAgent: false,
    t, reset: value => { result = value; },
  })();
  assert.equal(result.llmModel, '');
});

test('shared form defaults do not contain an unconfigured model', () => {
  const formSource = readFileSync(new URL('../src/hooks/useAgentForm.ts', import.meta.url), 'utf8');
  const context = { exports: {}, require: () => ({ useCallback: value => value,
    useState: value => [value, () => {}] }) };
  vm.runInNewContext(ts.transpileModule(formSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  assert.equal(context.exports.useAgentForm().formData.llmModel, '');
});

for (const options of [[], [{ value: 'provider-a:chat' }],
  [{ value: 'provider-a:chat' }, { value: 'provider-b:chat' }]]) {
  test(`a legacy model without provider requires explicit selection (${options.length} matches)`, async () => {
    let writes = 0;
    let closed = false;
    const feedback = [];
    const submit = callback('handleSubmit', {
      agent: saved, agentId: saved.id, formData: { ...saved, tools: [], llmModel: 'chat' },
      addedAiTools: [], aiTools: [], llmOptions: options, t, setIsUpdating() {},
      updateAgent: async () => { writes++; }, refreshAgents: async () => {},
      showToast: (...args) => feedback.push(args), onClose: () => { closed = true; },
    });
    await submit({ preventDefault() {} });
    assert.equal(writes, 0);
    assert.equal(closed, false);
    assert.equal(feedback[0][0], 'error');
  });
}

test('an explicit provider and colon-containing model is saved with all selected bindings', async () => {
  const writes = [];
  const formData = { ...saved, tools: [], profession: saved.role, toolConfigs: {},
    markdown: false, num_history_runs: 0 };
  const submit = callback('handleSubmit', {
    agent: saved, agentId: saved.id, formData, addedAiTools: [], aiTools: [], llmOptions: [], t,
    setIsUpdating() {}, updateAgent: async (...args) => writes.push(args),
    refreshAgents: async () => {}, showToast() {}, onClose() {},
  });
  await submit({ preventDefault() {} });
  assert.equal(writes.length, 1);
  const [id, payload] = writes[0];
  assert.equal(id, saved.id);
  assert.equal(payload.llmModel, saved.llmModel);
  assert.equal(payload.knowledgeBases, saved.knowledgeBases);
  assert.equal(payload.workflows, saved.workflows);
  assert.equal(payload.skills_enabled, false);
  assert.equal(payload.humanization_skill_name, 'brand-style');
  assert.equal(payload.humanization_skill_enabled, true);
});
