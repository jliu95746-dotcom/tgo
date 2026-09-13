import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const scope = { exports: {}, require() { return { default: { kindToProviderKey: kind => kind, buildBackendConfig: (_, params) => params } }; } };
vm.runInNewContext(ts.transpileModule(read('../src/utils/providerSetup.ts'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, scope);
const { existingModelConfigs, providerEditPatch, connectionDraft } = scope.exports;
const original = { id: 'existing', name: '品牌模型', kind: 'deepseek', apiKey: '', apiBaseUrl: 'https://example.test/v1', models: ['old'], modelTypes: { old: 'chat' }, modelConfigs: [{ id: 'old', name: 'old', type: 'chat', capabilities: { vision: true } }], defaultModel: 'old', enabled: false, params: { custom: 'retain' }, isFromStore: true };
test('opening and saving unchanged existing configuration produces no patch', () => {
  assert.equal(JSON.stringify(providerEditPatch(original, { ...original }, existingModelConfigs(original))), '{}');
});
test('renaming does not reset credentials, address, models, default or activation', () => {
  assert.equal(JSON.stringify(providerEditPatch(original, { ...original, name: '新名称' }, existingModelConfigs(original))), '{"name":"新名称"}');
});
test('adding a model retains existing capabilities and omits implicit default changes', () => {
  const patch = providerEditPatch(original, original, [...existingModelConfigs(original), { id: 'new', name: 'new', type: 'embedding' }]);
  assert.equal(patch.modelConfigs[0].capabilities.vision, true);
  assert.equal(patch.defaultModel, undefined); assert.equal(patch.apiKey, undefined);
});
test('stored-key previews omit blank and masked keys', () => {
  const result = connectionDraft(original); assert.equal(result.provider_id, 'existing'); assert.equal(result.api_key, undefined);
});
test('model store and split configuration modal are removed', () => {
  assert.equal(existsSync(new URL('../src/components/ai/ModelStoreModal.tsx', import.meta.url)), false);
  const source = read('../src/components/settings/ModelProvidersSettings.tsx');
  assert.doesNotMatch(source, /ModelStore|AddModelModal|testProvider\(/);
  assert.match(source, /onUsageChange=/);
});
for (const file of ['ProviderConfigModal', 'ProviderCard', 'ProviderModelPicker', 'ModelProvidersSettings', 'ModelRowActions']) {
  test(`${file} has valid TypeScript syntax`, () => {
    const result = ts.transpileModule(read(`../src/components/settings/${file}.tsx`), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX }, reportDiagnostics: true });
    assert.equal(result.diagnostics?.length, 0);
  });
}
