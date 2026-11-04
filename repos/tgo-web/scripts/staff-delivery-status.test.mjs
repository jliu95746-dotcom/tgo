import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function policies() {
  const source = readFileSync(new URL('../src/utils/staffDeliveryStatus.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const scope = { exports: {} };
  vm.runInNewContext(compiled, scope);
  return scope.exports;
}
const wechat = (status, history = 'pending') => ({
  client_msg_no: 'reply-b', delivery_transport: 'wechat_local', delivery_status: status, history_status: history,
});

test('cloud acceptance and IM history never become a WeChat sent label', () => {
  const { staffDeliveryLabel } = policies();
  assert.equal(staffDeliveryLabel(wechat('pending', 'sent')), 'chat.delivery.wechatPending');
  assert.equal(staffDeliveryLabel(wechat('processing', 'sent')), 'chat.delivery.wechatProcessing');
  assert.equal(staffDeliveryLabel(wechat('sent', 'sent')), 'chat.delivery.wechatSent');
});

test('pending and unknown WeChat replies keep checking for a late receipt', () => {
  const { shouldPollStaffDelivery } = policies();
  assert.equal(shouldPollStaffDelivery(wechat('pending', 'sent')), true);
  assert.equal(shouldPollStaffDelivery(wechat('unknown')), true);
  assert.equal(shouldPollStaffDelivery(wechat('sent', 'sent')), false);
  assert.equal(shouldPollStaffDelivery(wechat('failed')), false);
  assert.equal(shouldPollStaffDelivery(wechat('pending'), true), false);
});

test('revoked ownership stops actions and shows the handoff boundary', () => {
  assert.equal(policies().staffDeliveryLabel(wechat('pending'), true), 'chat.delivery.accessChanged');
});

test('failed WeChat reply asks for review instead of retrying the old task', () => {
  assert.equal(policies().staffDeliveryLabel(wechat('failed')), 'chat.delivery.wechatFailed');
});

test('a new manual submission only replaces a confirmed failed WeChat identity', () => {
  const { canReuseStaffDeliveryRequest } = policies();
  assert.equal(canReuseStaffDeliveryRequest(wechat('failed')), false);
  for (const status of ['pending', 'processing', 'unknown']) assert.equal(canReuseStaffDeliveryRequest(wechat(status)), true);
  assert.equal(canReuseStaffDeliveryRequest({ ...wechat('failed'), delivery_transport: 'platform' }), true);
  assert.equal(canReuseStaffDeliveryRequest(undefined), true);
});

test('legacy non-WeChat delivery and training labels remain unchanged', () => {
  const { staffDeliveryLabel, shouldPollStaffDelivery } = policies();
  const receipt = { client_msg_no: 'reply-b', delivery_status: 'sent', history_status: 'sent' };
  assert.equal(staffDeliveryLabel(receipt), null);
  assert.equal(staffDeliveryLabel({ ...receipt, training_status: 'saved' }), 'chat.delivery.trainingSaved');
  assert.equal(shouldPollStaffDelivery({ ...receipt, delivery_status: 'unknown' }), false);
});
