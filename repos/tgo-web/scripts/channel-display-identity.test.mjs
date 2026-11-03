import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/channelDisplayIdentity.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports });
const { resolveDisplayChannel, resolveDisplayProfile } = exports;
const visitorId = 'identity-fixture-1234-vtr';
const profile = { name: '被翻译的昵称', avatar: '/current-avatar.png', extra: { platform_type: 'wecom', nickname: 'Original Nickname' } };

test('message sender and conversation resolve to the same visitor profile cache', () => {
  for (const type of [1, 251]) {
    const result = resolveDisplayChannel(visitorId, type);
    assert.equal(result.channelId, visitorId);
    assert.equal(result.channelType, 251);
  }
  assert.equal(resolveDisplayChannel('employee-staff', 1).channelType, 1);
  assert.equal(resolveDisplayChannel('assistant-agent', 1).channelType, 1);
  assert.equal(resolveDisplayChannel('ordinary-person', 1).channelType, 1);
  assert.equal(resolveDisplayChannel(visitorId, 2).channelType, 2);
});

test('historical, realtime and list render the original WeCom nickname and current avatar', () => {
  for (const oldName of [undefined, '访客-vtr', '旧昵称', '被翻译的昵称']) {
    const resolved = resolveDisplayProfile(visitorId, profile, oldName, '/old-avatar.png');
    assert.equal(resolved.name, 'Original Nickname');
    assert.equal(resolved.avatar, '/current-avatar.png');
  }
  const updated = { ...profile, extra: { ...profile.extra, nickname: 'Updated Nickname' } };
  assert.equal(resolveDisplayProfile(visitorId, updated).name, 'Updated Nickname');
});

test('explicit customer names remain supported and missing nicknames use the saved name', () => {
  const named = { ...profile, extra: { ...profile.extra, name: '客户备注' } };
  assert.equal(resolveDisplayProfile(visitorId, named).name, '客户备注');
  assert.equal(resolveDisplayProfile(visitorId, { ...profile, extra: { platform_type: 'wecom' } }).name, profile.name);
  assert.equal(resolveDisplayProfile(visitorId, undefined, '已知顾客').name, '已知顾客');
  assert.equal(resolveDisplayProfile(visitorId, undefined).name, undefined);
  assert.equal(resolveDisplayProfile(visitorId, undefined, '访客-vtr').name, undefined);
});

test('staff, AI and other channels retain their own identities', () => {
  const employee = resolveDisplayProfile('employee-staff', { name: '员工资料', avatar: '/employee.png' }, '企微人工客服', '/native.png');
  assert.equal(employee.name, '企微人工客服');
  assert.equal(employee.avatar, '/native.png');
  const website = { ...profile, extra: { platform_type: 'website', nickname: '其他昵称' } };
  assert.equal(resolveDisplayProfile(visitorId, website).name, profile.name);
  assert.equal(resolveDisplayProfile('assistant-agent', profile, 'AI 客服').name, 'AI 客服');
});
