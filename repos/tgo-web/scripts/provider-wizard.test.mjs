import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
const read = name => readFileSync(new URL('../src/components/settings/' + name + '.tsx', import.meta.url), 'utf8');
test('service form displays vendor and models together without step navigation', () => {
  const modal = read('ProviderConfigModal');
  assert.doesNotMatch(modal, /step ===|nextStep|stepConnection|stepModels/);
  assert.match(modal, /modelSetup.vendorSection/);
  assert.match(modal, /ProviderModelPicker/);
  assert.doesNotMatch(modal, /ProviderDefaultChoices|onApplyUsage/);
  assert.match(modal, /modelSetup.finish/);
});
test('each model has row actions and no footer management or purpose summary', () => {
  const card = read('ProviderCard');
  assert.doesNotMatch(card, /<footer|modelSetup.manageService/);
  assert.match(card, /ModelRowActions/);
  assert.match(card, /onDelete\(provider.id, id\)/);
  assert.match(card, /onEdit\(provider, id\)/);
  assert.doesNotMatch(read('ModelProvidersSettings'), /modelSetup.allPurposes|removeProvider/);
  assert.match(read('ModelProvidersSettings'), /removeModelFromProvider\(target.providerId, target.modelId\)/);
});

test('unsynced defaults are not displayed as effective and can be retried', () => {
  const page = read('ModelProvidersSettings');
  assert.match(page, /conf.sync_status !== 'synced'\) throw new Error/);
  assert.match(page, /syncAIConfig\(targetProject\)/);
  assert.match(page, /changedModelUsage\(modelSelections, selections\)/);
  assert.doesNotMatch(read('ProviderCard'), /modelSetup.systemDefault/);
  assert.match(read('ProviderCard'), /modelSetup.pendingDefault/);
});
