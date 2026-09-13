import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const output = ts.transpileModule(read('../src/utils/platformPresentation.ts'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const scope = { exports: {} };
vm.runInNewContext(output, scope);
const displayStatus = scope.exports.platformDisplayStatus;

test('channel labels are concise and do not claim a connection check', () => {
  const zh = JSON.parse(read('../src/i18n/locales/zh.json'));
  assert.equal(zh.channelManagement.enabled, '已启用');
  assert.equal(zh.platforms.wecom.label, '企微客服');
  assert.doesNotMatch(read('../src/stores/platformStore.ts'), /未验证连接/);
});

test('active channels with missing configuration are not shown as connected', () => {
  assert.equal(displayStatus({ status: 'connected', is_configured: false }), 'unconfigured');
  assert.equal(displayStatus({ status: 'connected' }), 'unconfigured');
});
test('complete configuration means enabled, not verified connectivity', () => {
  assert.equal(displayStatus({ status: 'connected', is_configured: true }), 'enabled');
});
test('disabled and error states are preserved', () => {
  assert.equal(displayStatus({ status: 'disabled', is_configured: true }), 'disabled');
  assert.equal(displayStatus({ status: 'error', is_configured: true }), 'error');
});
test('selecting a channel type asks for a name before creating', () => {
  const source = read('../src/components/platforms/PlatformList.tsx');
  const select = source.split('const handleTypeSelect =')[1].split('const handleCreate =')[0];
  assert.match(select, /setPendingType/);
  assert.doesNotMatch(select, /createPlatform\(/);
  const modal = read('../src/components/platforms/PlatformNameModal.tsx');
  assert.match(modal, /!cleanName \|\| duplicate \|\| busy/);
});
test('no random-success connection tester remains in the channel store', () => {
  assert.doesNotMatch(read('../src/stores/platformStore.ts'), /testPlatformConnection|Math\.random\(\)\s*[<>]/);
});
