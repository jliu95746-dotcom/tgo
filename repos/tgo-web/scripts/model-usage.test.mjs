import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const scope = { exports: {} };
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
vm.runInNewContext(ts.transpileModule(read('../src/utils/modelUsage.ts'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, scope);
const { toggleModelUsage, changedModelUsage } = scope.exports;
const saved = { chat: 'provider:old', embedding: 'embed:keep', asr: '', ocr: '', vlm: '' };
test('choosing within a provider replaces only the same purpose', () => {
  const next = toggleModelUsage(saved, 'chat', 'other:new');
  assert.equal(next.chat, 'other:new');
  assert.equal(next.embedding, saved.embedding);
  assert.equal(saved.chat, 'provider:old');
});
test('clicking selected purpose clears only that purpose', () => {
  assert.equal(toggleModelUsage(saved, 'chat', saved.chat).chat, '');
});
test('unchanged purposes are never submitted; model IDs retain colons', () => {
  assert.equal(JSON.stringify(changedModelUsage(saved, saved)), '{}');
  const patch = changedModelUsage(saved, { ...saved, chat: 'provider:model:version' });
  assert.equal(JSON.stringify(patch), '{"default_chat_provider_id":"provider","default_chat_model":"model:version"}');
});
test('clearing a purpose explicitly submits nulls', () => {
  const patch = changedModelUsage(saved, { ...saved, chat: '' });
  assert.equal(patch.default_chat_provider_id, null);
  assert.equal(patch.default_chat_model, null);
});
test('purpose controls live inside service cards, not a second picker', () => {
  const page = read('../src/components/settings/ModelProvidersSettings.tsx');
  assert.doesNotMatch(page, /renderDefaultModelField|<details/);
  assert.match(page, /onUsageChange=/);
  const card = read('../src/components/settings/ProviderCard.tsx');
  assert.doesNotMatch(card, /aria-pressed/);
  assert.doesNotMatch(card, /modelSetup.systemDefault/);
  assert.match(card, /<select/);
  assert.doesNotMatch(page, /modelSetup.saveUsage/);
  assert.match(page, /pendingUsage/);
});
