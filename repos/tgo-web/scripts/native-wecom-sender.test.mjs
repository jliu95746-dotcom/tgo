import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/nativeWecomSender.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports });
const { isNativeWecomStaffPayload } = exports;

test('native human text and card placeholders retain staff identity', () => {
  for (const content of ['在的亲', '[名片消息，内容需查看原消息]']) {
    assert.equal(isNativeWecomStaffPayload({ type: 1, content,
      extra: { source: 'wecom_native', native_role: 'staff', servicer_userid: 'test-member' } }), true);
  }
});

test('customer, AI and malformed payloads are not relabelled as native staff', () => {
  for (const payload of [null, 'staff', [], { sender_kind: 'ai' },
    { extra: { native_role: 'staff' } },
    { extra: { source: 'wecom_native', native_role: 'customer' } },
    { extra: [] }]) {
    assert.equal(isNativeWecomStaffPayload(payload), false);
  }
});
