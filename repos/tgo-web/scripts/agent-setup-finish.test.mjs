import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/ai/CreateAgentModal.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('CreateAgentModal.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let handler;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'handleSubmit') handler = node.initializer.getText(ast);
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(handler, 'Exercise the real wizard submit handler');
const translations = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/i18n/businessScenarios.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: translations });

function harness(options = {}) {
  const calls = [], toasts = [];
  const context = {
    isSetupBusy: false, isTrialPending: false, step: 'activation',
    useAgentActivationStore: { getState: () => ({ entries: {} }) },
    createAgentFormData: { name: '已有客服' },
    prepareForTrial: async () => { calls.push('save'); return { agent_id: 'original-agent' }; },
    nextStep: () => calls.push('next'), setStep: step => calls.push(step),
    discardAndClose: () => calls.push('close'),
    showToast: (...args) => toasts.push(args),
    refreshAgents: async () => { calls.push('refresh'); if (options.refreshFails) throw new Error('list unavailable'); },
    t: (key, values = {}) => {
      const value = key.split('.').slice(1).reduce((node, part) => node?.[part], translations.businessScenariosZh) || key;
      return typeof value === 'string' ? value.replace('{{name}}', values.name || '') : key;
    },
    ...options,
  };
  const compiled = ts.transpileModule(`const submit = ${handler};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return { calls, toasts, run: () => vm.runInNewContext(`${compiled}\nsubmit({ preventDefault() {} })`, context) };
}

test('finishing a recovered employee confirms saved configuration and does not claim another creation', async () => {
  const h = harness({ refreshFails: true });
  await h.run();
  assert.deepEqual(h.calls, ['save', 'close', 'refresh']);
  assert.equal(h.toasts[0][0], 'success');
  assert.equal(h.toasts[0][1], '配置已保存');
  assert.match(h.toasts[0][2], /已有客服.*待启用/);
});

test('intermediate wizard steps save without closing or showing the final success message', async () => {
  for (const [step, expected] of [['resources', 'testing'], ['testing', 'activation']]) {
    const h = harness({ step });
    await h.run();
    assert.deepEqual(h.calls, ['save', expected]);
    assert.equal(h.toasts.length, 0);
  }
});

test('an unconfirmed save or pending mutation cannot finish the employee setup', async () => {
  for (const options of [
    { prepareForTrial: async () => null }, { isSetupBusy: true }, { isTrialPending: true },
    { useAgentActivationStore: { getState: () => ({ entries: { employee: { writing: true } } }) } },
  ]) {
    const h = harness(options);
    await h.run();
    assert.equal(h.calls.length, 0);
    assert.equal(h.toasts.length, 0);
  }
});
