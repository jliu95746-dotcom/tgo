import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual handler from the component, with React state and transports
// replaced by controlled fixtures. This is a handler test, not a browser test.
const source = readFileSync(new URL('../src/components/chat/MessageInput.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('MessageInput.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let handler;
function find(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'handleSend') handler = node.initializer;
  ts.forEachChild(node, find);
}
find(ast);
assert.ok(handler, 'MessageInput must expose its send handler');
const compiled = ts.transpileModule(`globalThis.send = ${handler.getText(ast)};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function fixture(onSendMessage, overrides = {}) {
  const state = { text: '没有绿色。', draft: '抱歉，没有绿色。', samples: [], errors: [], sending: false };
  const scope = {
    // Preserve the legacy transport contract; durable customer-channel cases are below.
    channelType: 1, visitorExtra: undefined,
    isManualDisabled: false, isSending: false, isSendingLocal: false,
    isStreamingInProgress: false, pastedItems: [], selectedFiles: [],
    shouldMaintainFocus: { current: false }, sendInFlightRef: { current: false },
    message: state.text, assistDraft: state.draft, onSendMessage,
    isAssistMode: true, isHumanizationEnabled: true, selectedHumanizationSkill: 'style-a',
    latestVisitorMessage: { content: '最新的问题不应覆盖草稿的原问题。' },
    assistSourceMessageId: 'incoming-1',
    assistTrainingSource: { customerMessage: '有绿色吗？', skillName: 'style-a', recentMessages: [] },
    setMessage: value => { state.text = typeof value === 'function' ? value(state.text) : value; },
    setAssistDraft: value => { state.draft = value; },
    setAssistSourceMessageId() {}, setAssistTrainingSource() {},
    setIsSendingLocal: value => { state.sending = value; }, maintainTextareaFocus() {},
    SkillsApiService: { addHumanizationTrainingSample: async (...args) => { state.samples.push(args); } },
    showSuccess() {}, showToast() {}, showError: (...args) => state.errors.push(args),
    t: (_key, fallback) => fallback, getErrorMessage: error => error.message,
    ...overrides,
  };
  vm.runInNewContext(compiled, scope);
  return { state, send: scope.send };
}

test('durable assist corrections travel with the send request instead of a second browser API call', async () => {
  let sentIntent;
  const { state, send } = fixture(async (_message, intent) => { sentIntent = intent; return true; }, { channelType: 251 });
  await send();
  assert.equal(sentIntent.skill_name, 'style-a');
  assert.equal(sentIntent.customer_message, '有绿色吗？');
  assert.equal(sentIntent.source_message_id, 'incoming-1');
  assert.equal(sentIntent.ai_draft, '抱歉，没有绿色。');
  assert.equal(state.samples.length, 0);
  assert.equal(state.text, '');
});

test('an uncertain durable send retains the draft but keeps the correction on the original request', async () => {
  let sentIntent;
  const { state, send } = fixture(async (_message, intent) => { sentIntent = intent; return false; }, { channelType: 251 });
  await send();
  assert.equal(sentIntent.skill_name, 'style-a');
  assert.equal(state.text, '没有绿色。');
  assert.equal(state.samples.length, 0);
});

for (const attachment of ['images', 'file']) {
  test(`durable rich ${attachment} includes its correction before sending`, async () => {
    let sentIntent;
    const capture = async intent => { sentIntent = intent; return true; };
    const { state, send } = fixture(undefined, {
      channelType: 251,
      pastedItems: attachment === 'images' ? [{}] : [],
      selectedFiles: attachment === 'file' ? [{}] : [],
      sendRichTextWithImages: capture, sendRichTextWithFile: capture,
    });
    await send();
    assert.equal(sentIntent.skill_name, 'style-a');
    assert.equal(state.samples.length, 0);
  });
}

for (const result of [false, undefined]) {
  test(`an unconfirmed send (${result}) preserves draft and never trains`, async () => {
    const { state, send } = fixture(async () => result);
    await send();
    assert.equal(state.text, '没有绿色。');
    assert.equal(state.samples.length, 0);
    assert.equal(state.draft, '抱歉，没有绿色。');
  });
}

for (const attachment of ['images', 'file']) {
  for (const confirmed of [true, false]) {
    test(`edited rich ${attachment} trains only after confirmed delivery (${confirmed})`, async () => {
      const { state, send } = fixture(undefined, {
        pastedItems: attachment === 'images' ? [{}] : [],
        selectedFiles: attachment === 'file' ? [{}] : [],
        sendRichTextWithImages: async () => confirmed,
        sendRichTextWithFile: async () => confirmed,
      });
      await send();
      assert.equal(state.samples.length, confirmed ? 1 : 0);
      assert.equal(state.text, confirmed ? '' : '没有绿色。');
    });
  }
}

test('success stores the edited reply with the original question and selected skill', async () => {
  const { state, send } = fixture(async () => true);
  await send();
  assert.equal(state.text, '');
  assert.equal(state.samples.length, 1);
  assert.equal(state.samples[0][0], 'style-a');
  assert.equal(state.samples[0][1].customer_message, '有绿色吗？');
  assert.equal(state.samples[0][1].source_message_id, 'incoming-1');
  assert.equal(state.samples[0][1].final_reply, '没有绿色。');
});

test('changing the selected skill does not train the new skill on an old draft', async () => {
  const { state, send } = fixture(async () => true, { selectedHumanizationSkill: 'style-b' });
  await send();
  assert.equal(state.samples.length, 0);
});

test('a missing send callback never clears or trains a draft', async () => {
  const { state, send } = fixture(undefined);
  await send();
  assert.equal(state.text, '没有绿色。');
  assert.equal(state.samples.length, 0);
});

test('an unmodified draft is sent but not treated as a correction', async () => {
  const { state, send } = fixture(async () => true, { assistDraft: '没有绿色。' });
  await send();
  assert.equal(state.text, '');
  assert.equal(state.samples.length, 0);
});

test('failed training never undoes or repeats a successful delivery', async () => {
  let deliveries = 0;
  const { state, send } = fixture(async () => { deliveries += 1; return true; }, {
    SkillsApiService: { addHumanizationTrainingSample: async () => { throw new Error('training unavailable'); } },
  });
  await send();
  assert.equal(deliveries, 1);
  assert.equal(state.text, '');
  assert.equal(state.draft, null);
  assert.equal(state.errors.length, 1);
  assert.equal(state.errors[0][0], '回复已发送，但训练记录失败');
  assert.equal(state.sending, false);
});

test('a rejected delivery unlocks the handler without saving a sample', async () => {
  const { state, send } = fixture(async () => { throw new Error('delivery failed'); });
  await assert.rejects(send, /delivery failed/);
  assert.equal(state.text, '没有绿色。');
  assert.equal(state.samples.length, 0);
  assert.equal(state.sending, false);
});

test('repeated clicks while sending produce one delivery and one sample', async () => {
  let resolve;
  let deliveries = 0;
  const pending = new Promise(done => { resolve = done; });
  const { state, send } = fixture(() => { deliveries += 1; return pending; });
  const first = send();
  const second = send();
  resolve(true);
  await Promise.all([first, second]);
  assert.equal(deliveries, 1);
  assert.equal(state.samples.length, 1);
  assert.equal(state.sending, false);
});

test('send completion never erases text entered while the request was pending', async () => {
  let resolve;
  const { state, send } = fixture(() => new Promise(done => { resolve = done; }));
  const pending = send();
  state.text = '新输入的内容';
  resolve(true);
  await pending;
  assert.equal(state.text, '新输入的内容');
});
