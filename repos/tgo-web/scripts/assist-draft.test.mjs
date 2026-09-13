import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const utilitySource = readFileSync(new URL('../src/utils/assistDraftContext.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(utilitySource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports });
const { getAssistDraftSource, getAssistSourceId, canApplyAssistDraft, getAssistCustomerInput } = exports;

test('media drafts use the stored file reference instead of display placeholders', () => {
  assert.deepEqual(JSON.parse(JSON.stringify(getAssistCustomerInput({
    content: '[图片]', payloadType: 2, payload: { type: 2, url: '/v1/chat/files/file-id' },
  }))), { customerMessage: '/v1/chat/files/file-id', messageType: 2 });
  assert.deepEqual(JSON.parse(JSON.stringify(getAssistCustomerInput({
    content: '[语音]', payloadType: 4, payload: { type: 4, url: '/v1/chat/files/audio-id' },
  }))), { customerMessage: '/v1/chat/files/audio-id', messageType: 4 });
  assert.equal(getAssistCustomerInput({ content: '[图片]', payloadType: 2 }), null);
  assert.equal(getAssistCustomerInput({ content: '[文件]', payloadType: 3 }), null);
  assert.equal(getAssistCustomerInput({ content: '  你好  ', payloadType: 1 }).customerMessage, '你好');
});

test('audio file messages and cloud images preserve the uploaded file identity', () => {
  for (const [type, mime, expected] of [[3, 'audio/wav', 4], [2, 'image/png', 2]]) {
    const input = getAssistCustomerInput({ content: '[文件]', payloadType: type,
      payload: { type, mime_type: mime, file_id: 'owned-upload', url: 'https://storage.test/file' },
    });
    assert.equal(input.messageType, expected);
    assert.equal(input.mediaFileId, 'owned-upload');
    assert.equal(input.customerMessage, 'https://storage.test/file');
  }
});

function message(id, type, seq, seconds = seq, extra = {}) {
  return {
    id, type, content: id, messageSeq: seq,
    timestamp: new Date(seconds * 1000).toISOString(), ...extra,
  };
}

test('answered historical questions do not become drafts after reload', () => {
  const source = message('question', 'visitor', 10);
  for (const status of ['sent', 'delivered', 'read']) {
    assert.equal(getAssistDraftSource([source, message('reply', 'staff', 12, 12, { status })]), undefined);
  }
});

test('system notices do not hide an unanswered question', () => {
  const source = message('question', 'visitor', 10);
  assert.equal(getAssistDraftSource([source, message('handoff', 'system', 11)]), source);
});

test('a new customer question after a reply is eligible', () => {
  const source = message('new question', 'visitor', 12);
  assert.equal(getAssistDraftSource([
    message('old question', 'visitor', 10), source, message('reply', 'staff', 11),
  ]), source);
});

test('sequence wins over timestamps when both messages have server sequence numbers', () => {
  assert.equal(getAssistDraftSource([
    message('question', 'visitor', 10, 100), message('reply', 'staff', 11, 99),
  ]), undefined);
});

test('an outgoing local message without a sequence suppresses automatic regeneration', () => {
  assert.equal(getAssistDraftSource([
    message('question', 'visitor', 10), message('pending reply', 'staff', undefined, 12),
  ]), undefined);
});

test('failed and uncertain outgoing replies remain explicit retry work, not new automatic drafts', () => {
  for (const extra of [{ status: 'failed' }, { metadata: { delivery_managed: true } }]) {
    assert.equal(getAssistDraftSource([
      message('question', 'visitor', 10), message('outgoing reply', 'staff', undefined, 12, extra),
    ]), undefined);
  }
});

test('a stale duplicate realtime message cannot outrank an acknowledged history copy', () => {
  assert.equal(getAssistDraftSource([
    message('question', 'visitor', 10, 10, { clientMsgNo: 'visitor-1' }),
    message('reply', 'staff', 11),
    message('question-copy', 'visitor', undefined, 20, { clientMsgNo: 'visitor-1' }),
  ]), undefined);
});

test('empty and system-only histories have no draft source', () => {
  assert.equal(getAssistDraftSource([]), undefined);
  assert.equal(getAssistDraftSource([message('notice', 'system', 1)]), undefined);
});

test('source identity uses stable platform/client/message identifiers before display time', () => {
  assert.equal(getAssistSourceId({ sourceMessageId: 'external', clientMsgNo: 'client' }), 'external');
  assert.equal(getAssistSourceId({ clientMsgNo: 'client', id: 'local' }), 'client');
  assert.equal(getAssistSourceId({ messageId: 'server', id: 'local' }), 'server');
  assert.equal(getAssistSourceId({ id: 'local', timestamp: 'time' }), 'local');
  assert.equal(getAssistSourceId(undefined), null);
});

const request = {
  channelId: 'one', sourceId: 'question', text: '', enabled: true, skillName: 'style-a',
};

test('a draft is applied only while its input, channel, source, mode and skill still match', () => {
  assert.equal(canApplyAssistDraft(request, { ...request }), true);
  for (const changed of [
    { text: '人工正在输入' }, { channelId: 'two' }, { sourceId: 'new question' },
    { sourceId: null }, { enabled: false }, { skillName: 'style-b' },
  ]) {
    assert.equal(canApplyAssistDraft(request, { ...request, ...changed }), false);
  }
});

// Execute the actual async component callback, not a copy of its implementation.
const component = readFileSync(new URL('../src/components/chat/MessageInput.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('MessageInput.tsx', component, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'generateAssistDraft') {
    callback = node.initializer.arguments[0];
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(callback);
const compiled = ts.transpileModule(`globalThis.generate = ${callback.getText(ast)};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function draftFixture() {
  let finish;
  const state = { text: '', draft: null, loading: false, errors: [], trainingSource: null, request: null };
  const latest = message('question', 'visitor', 1);
  const context = { current: { ...request } };
  const scope = {
    visitorId: 'visitor', latestVisitorMessage: latest, isAssistMode: true, styleReady: true,
    isHumanizationEnabled: true, selectedHumanizationSkill: 'style-a',
    assistRequestIdRef: { current: 0 }, assistContextRef: context,
    getAssistSourceId, canApplyAssistDraft, getAssistCustomerInput,
    setIsGeneratingDraft: value => { state.loading = value; },
    chatMessagesApiService: { generateAssistDraft: request => new Promise(resolve => {
      state.request = request; finish = resolve;
    }) },
    setMessage: value => { state.text = value; }, setAssistDraft: value => { state.draft = value; },
    setAssistSourceMessageId() {}, setAssistTrainingSource(value) { state.trainingSource = value; }, maintainTextareaFocus() {},
    showApiError: (_toast, error) => state.errors.push(error), showToast() {},
  };
  vm.runInNewContext(compiled, scope);
  return { state, scope, context, finish: (extra = {}) => finish({ draft: 'AI 新草稿', source_message_id: 'question', ...extra }) };
}

test('draft generation waits until employee style settings are known', async () => {
  const f = draftFixture();
  f.scope.styleReady = false;
  await f.scope.generate();
  assert.equal(f.state.request, null);
  assert.equal(f.state.loading, false);
});

test('actual draft callback does not overwrite input changed while the model was running', async () => {
  const f = draftFixture();
  const pending = f.scope.generate();
  f.state.text = '人工正在输入';
  f.context.current = { ...f.context.current, text: f.state.text };
  f.finish();
  await pending;
  assert.equal(f.state.text, '人工正在输入');
  assert.equal(f.state.draft, null);
  assert.equal(f.state.loading, false);
});

test('actual draft callback ignores replies for a now-answered or different question', async () => {
  for (const sourceId of [null, 'new question']) {
    const f = draftFixture();
    const pending = f.scope.generate();
    f.context.current = { ...f.context.current, sourceId };
    f.finish();
    await pending;
    assert.equal(f.state.draft, null);
  }
});

test('actual draft callback still fills an unchanged editor', async () => {
  const f = draftFixture();
  const pending = f.scope.generate();
  f.finish();
  await pending;
  assert.equal(f.state.text, 'AI 新草稿');
  assert.equal(f.state.draft, 'AI 新草稿');
});

test('actual media draft callback trains on recognized customer text instead of the file URL', async () => {
  const f = draftFixture();
  f.scope.latestVisitorMessage = { ...f.scope.latestVisitorMessage, content: '[图片]', payloadType: 2,
    payload: { type: 2, url: 'https://storage.test/owned-image', file_id: 'owned-upload' } };
  const pending = f.scope.generate();
  assert.equal(f.state.request.message_type, 2);
  assert.equal(f.state.request.media_file_id, 'owned-upload');
  assert.equal(f.state.request.customer_message, 'https://storage.test/owned-image');
  f.finish({ customer_message: '识别内容：订单 A1001' });
  await pending;
  assert.equal(f.state.trainingSource.customerMessage, '识别内容：订单 A1001');
  assert.equal(f.state.text, 'AI 新草稿');
});

test('the actual chat selector waits for history and delivery restore, including search windows', () => {
  const source = readFileSync(new URL('../src/components/layout/ChatWindow.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('ChatWindow.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let selector;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'latestVisitorMessage') {
      selector = node.initializer.arguments[0];
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(selector);
  const compiled = ts.transpileModule(`globalThis.select = ${selector.getText(tree)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText;
  const pending = message('unanswered', 'visitor', 1);
  const baseline = {
    deliveryReady: true, hasLoadedHistory: true, isLoadingHistory: false,
    historyError: null, hasNewerHistory: false,
    historicalMessages: [pending], realtimeMessages: [],
    convertWuKongIMToMessage: item => item, getAssistDraftSource,
  };
  for (const [changes, expected] of [
    [{}, pending], [{ deliveryReady: false }, undefined], [{ hasLoadedHistory: false }, undefined],
    [{ isLoadingHistory: true }, undefined], [{ historyError: 'unavailable' }, undefined],
    [{ hasNewerHistory: true }, undefined],
    [{ realtimeMessages: [message('reply', 'staff', 2)] }, undefined],
  ]) {
    const scope = { ...baseline, ...changes };
    vm.runInNewContext(compiled, scope);
    assert.equal(scope.select(), expected);
  }
  assert.match(source, /<MessageInput\s+key=\{historyChannelKey \|\| activeChat\.id\}/);
});
