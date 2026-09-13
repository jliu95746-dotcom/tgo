import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/customerMessageDelivery.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
function fixture(status = 'sent') {
  const calls = { server: [], legacy: 0, ws: 0, patches: [] };
  const scope = { exports: {}, require: name => {
    if (name === '@/i18n') return { default: { t: key => key } };
    if (name.endsWith('/chatMessagesApi')) return { chatMessagesApiService: { staffSendPlatformMessage: async () => { calls.legacy++; } } };
    if (name.endsWith('/staffDeliveryStore')) return { useStaffDeliveryStore: { getState: () => ({
      submit: async (...args) => { calls.server.push(args); return { delivery_status: status, history_status: 'pending' }; },
    }) } };
    throw new Error(`Unexpected dependency: ${name}`);
  } };
  vm.runInNewContext(compiled, scope);
  return { calls, send: overrides => scope.exports.sendCustomerMessage({
    staffId: 'staff-a', channelId: 'visitor-vtr', channelType: 251, clientMsgNo: 'local-a',
    payload: { type: 1, content: '你好' }, platformType: 'custom', isConnected: false,
    sendWsMessage: async () => { calls.ws++; }, updateMessage: (...args) => calls.patches.push(args),
    ...overrides,
  }) };
}
for (const type of [1, 2, 3, 12]) {
  test(`payload type ${type} uses server delivery without browser WebSocket`, async () => {
    const { calls, send } = fixture();
    assert.equal(await send({ payload: { type, content: 'isolated' } }), true);
    assert.equal(calls.server.length, 1);
    assert.equal(calls.server[0][1].client_msg_no, 'local-a');
    assert.equal(calls.ws, 0);
    assert.equal(calls.legacy, 0);
    assert.equal(calls.patches[0][1].metadata.ws_send_error, false);
  });
}
test('website delivery uses the same server authority even with disconnected browser WS', async () => {
  const { calls, send } = fixture();
  assert.equal(await send({ platformType: 'website' }), true);
  assert.equal(calls.server.length, 1);
  assert.equal(calls.ws, 0);
});
for (const status of ['pending', 'processing', 'failed', 'unknown']) {
  test(`${status} is not reported as confirmed delivery and never falls back to WS`, async () => {
    const { calls, send } = fixture(status);
    assert.equal(await send(), false);
    assert.equal(calls.ws, 0);
    assert.equal(calls.legacy, 0);
  });
}
test('non-customer channels keep the existing REST then WS transport', async () => {
  const { calls, send } = fixture();
  assert.equal(await send({ channelType: 1, platformType: 'custom', isConnected: true }), true);
  assert.equal(calls.server.length, 0);
  assert.equal(calls.legacy, 1);
  assert.equal(calls.ws, 1);
});

test('the durable server request carries the immutable training intent', async () => {
  const { calls, send } = fixture();
  const training = { skill_name: 'style-a', customer_message: '原问题', ai_draft: '原草稿', recent_messages: [] };
  await send({ training });
  assert.equal(calls.server[0][1].training, training);
});

test('restored rich messages validate attachment fields before rendering', () => {
  const payloadSource = readFileSync(new URL('../src/utils/staffDeliveryPayload.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(payloadSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const scope = { exports: {}, require: () => ({ MessagePayloadType: { TEXT: 1, IMAGE: 2, FILE: 3, RICH_TEXT: 12 } }) };
  vm.runInNewContext(code, scope);
  const parse = scope.exports.restoreDeliveryPayload;
  const rich = parse({ type: 12, content: '你好', images: [{ url: '/image.png' }, { url: 123 }], file: { url: '/file.pdf', name: 'file.pdf', size: 12 } });
  assert.equal(rich.images.length, 1);
  assert.equal(rich.file.name, 'file.pdf');
  assert.equal(parse({ type: 2, url: 123 }), undefined);
  assert.equal(parse({ type: 3, url: '/file.pdf', name: null }), undefined);
  assert.equal(parse({ type: 9999 }), undefined);
});
