import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const file = '../src/components/platforms/WeChatPersonalPlatformConfig.tsx';
const source = readFileSync(new URL(file, import.meta.url), 'utf8');
const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
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
const declaration = (name) => find((node) => ts.isVariableDeclaration(node) && node.name.getText(tree) === name);
function evaluate(expression, scope) {
  const output = ts.transpileModule(`globalThis.result = ${expression};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(output, scope);
  return scope.result;
}

test('the existing page initializes its selector from the shared single-agent field', () => {
  const initializer = declaration('[aiAgentIds, setAiAgentIds]').initializer.arguments[0].getText(tree);
  for (const agentId of ['owned-agent', null, undefined]) {
    const result = evaluate(initializer, { platform: { agent_id: agentId } });
    assert.deepEqual(Array.from(result), agentId ? [agentId] : []);
  }
});

test('a changed platform refreshes the selector using agent_id', () => {
  const effect = find((node) => ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect'
    && node.arguments[0].getText(tree).includes('setAiAgentIds'));
  let selected;
  const run = evaluate(effect.arguments[0].getText(tree), {
    platform: { agent_id: 'replacement', ai_mode: 'off', fallback_to_ai_timeout: null },
    setAiAgentIds: (value) => { selected = value; }, setAiMode() {}, setFallbackTimeout() {},
  });
  run();
  assert.deepEqual(Array.from(selected), ['replacement']);
  assert.match(effect.arguments[1].getText(tree), /platform\.agent_id\b/);
});

test('unchanged AI settings are not marked dirty and comparison never mutates selection', () => {
  const compare = declaration('hasAISettingsChanged').initializer.arguments[0].getText(tree);
  const platform = { agent_id: 'owned-agent', ai_mode: 'off', fallback_to_ai_timeout: null };
  const aiAgentIds = Object.freeze(['owned-agent']);
  assert.equal(evaluate(compare, { platform, aiAgentIds, aiMode: 'off', fallbackTimeout: null })(), false);
  assert.equal(evaluate(compare, { platform, aiAgentIds: ['replacement'], aiMode: 'off', fallbackTimeout: null })(), true);
  assert.equal(evaluate(compare, { platform, aiAgentIds: Object.freeze(['z', 'a']), aiMode: 'off', fallbackTimeout: null })(), true);
});

for (const selection of [['replacement'], []]) {
  test(`the actual save callback writes ${selection.length ? 'a single agent' : 'explicit null'} without enabling the channel`, async () => {
    const calls = [];
    const run = evaluate(declaration('handleSave').initializer.getText(tree), {
      platform: { id: 'fixture-platform' }, aiAgentIds: selection,
      aiMode: 'off', fallbackTimeout: 30,
      hasConfigChanges: false, hasNameChanged: false, hasAISettingsChanged: true,
      formValues: { agentbayApiKey: 'fixture-only', imageId: 'fixture-image', visionFullId: 'fixture:vision', reasoningFullId: 'fixture:reasoning' },
      updatePlatform: async (id, data) => { calls.push({ id, data }); },
      showToast() {}, showSuccess() {}, showApiError: (_toast, error) => { throw error; },
      t: (_key, fallback) => fallback,
      enablePlatform: () => { throw new Error('Must not enable the channel'); },
    });
    await run();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].id, 'fixture-platform');
    assert.equal(calls[0].data.agent_id, selection[0] ?? null);
    assert.equal(Object.hasOwn(calls[0].data, 'agent_ids'), false);
    assert.equal(calls[0].data.ai_mode, 'off');
    assert.equal(calls[0].data.fallback_to_ai_timeout, null);
    assert.equal(Object.hasOwn(calls[0].data, 'config'), false);
  });
}
