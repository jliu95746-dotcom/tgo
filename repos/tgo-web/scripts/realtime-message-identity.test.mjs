import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/wukongimApi.ts', import.meta.url), 'utf8');
const ast = ts.createSourceFile('wukongimApi.ts', source, ts.ScriptTarget.Latest, true);
const utilityClass = ast.statements.find(node => ts.isClassDeclaration(node)
  && node.name?.text === 'WuKongIMUtils');
assert.ok(utilityClass);
const scope = {
  exports: {}, atob,
  CHANNEL_TYPE: { PERSON: 1, GROUP: 2, CUSTOMER_SERVICE: 3 },
  DEFAULT_CHANNEL_TYPE: 1,
  MESSAGE_SENDER_TYPE: { STAFF: 'staff', VISITOR: 'visitor', SYSTEM: 'system' },
  STAFF_UID_SUFFIX: '-staff',
  PlatformType: { WECHAT: 'wechat', WEBSITE: 'website' },
  MessagePayloadType: { TEXT: 1, IMAGE: 2, FILE: 3, COMMAND: 99, STREAM: 100, RICH_TEXT: 12 },
  isSystemMessageType: type => type >= 1000 && type < 2000,
  toAbsoluteApiUrl: url => url,
};
vm.runInNewContext(ts.transpileModule(utilityClass.getText(ast), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, scope);
const { WuKongIMUtils } = scope.exports;
const assistScope = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(
  new URL('../src/utils/assistDraftContext.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, assistScope);
const { getAssistDraftSource } = assistScope.exports;

// easyjssdk RecvMessage uses camelCase; history HTTP responses use snake_case.
function realtime(fromUid, seq = 2, payload = { type: 1, content: 'test reply' }) {
  return { fromUid, messageId: `server-${seq}`, clientMsgNo: `client-${seq}`,
    messageSeq: seq, timestamp: seq, channelId: 'test-vtr', channelType: 251,
    header: {}, payload: btoa(JSON.stringify(payload)) };
}

test('actual converter identifies SDK staff and system senders, including encoded payloads', () => {
  for (const [uid, type] of [['operator-staff', 'staff'], ['system', 'system'], ['test-visitor', 'visitor']]) {
    const input = realtime(uid, 2, { type: 1, content: 'test reply' });
    const converted = WuKongIMUtils.convertToMessage(input);
    assert.equal(converted.type, type);
    assert.equal(converted.fromUid, uid);
    assert.equal(converted.clientMsgNo, input.clientMsgNo);
    assert.equal(converted.messageSeq, 2);
    assert.equal(converted.content, 'test reply');
  }
});

test('SDK and history copies agree on sender, identity, and channel', () => {
  const input = realtime('operator-staff', 2, { type: 1, content: 'test reply' });
  const live = WuKongIMUtils.convertToMessage(input);
  const history = WuKongIMUtils.convertToMessage({
    from_uid: input.fromUid, message_id_str: input.messageId, client_msg_no: input.clientMsgNo,
    message_seq: input.messageSeq, channel_id: input.channelId, channel_type: input.channelType,
    timestamp: input.timestamp, payload: { type: 1, content: 'test reply' },
  });
  for (const field of ['type', 'fromUid', 'id', 'clientMsgNo', 'messageSeq', 'channelId', 'channelType']) {
    assert.equal(live[field], history[field], field);
  }
});

test('server echo replaces an optimistic staff reply without creating a new customer turn', () => {
  const question = WuKongIMUtils.convertToMessage(realtime('visitor', 1, { type: 1, content: 'question' }));
  const echo = WuKongIMUtils.convertToMessage(realtime('operator-staff', 2, { type: 1, content: 'reply' }));
  const optimistic = { ...echo, type: 'staff', messageSeq: 0 };
  assert.equal(getAssistDraftSource([question, optimistic, echo]), undefined);
  // Once the echo overwrites the optimistic entry in messageStore, it must still be outgoing.
  assert.equal(getAssistDraftSource([question, echo]), undefined);
  const nextQuestion = WuKongIMUtils.convertToMessage(realtime('visitor', 3, { type: 1, content: 'next' }));
  assert.equal(getAssistDraftSource([question, echo, nextQuestion]), nextQuestion);
});

test('SDK system notices do not hide the latest unanswered customer question', () => {
  const question = WuKongIMUtils.convertToMessage(realtime('visitor', 1, { type: 1, content: 'question' }));
  const notice = WuKongIMUtils.convertToMessage(realtime('system', 2, { type: 1000, content: 'assigned' }));
  assert.equal(getAssistDraftSource([question, notice]), question);
});
