import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function implementation(kind, name) {
  const file = kind === 'widget' ? '../../tgo-widget-js/src/store/chatStore.ts'
    : '../../tgo-widget-miniprogram/src/core/chatStore.js';
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  let action;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === `IMService.${name}`) action = node.arguments[0];
    if (ts.isPropertyAssignment(node) && node.name.getText(ast) === name) action = node.initializer;
    if (ts.isBinaryExpression(node) && node.left.getText(ast) === `ChatStore.prototype.${name}`) action = node.right;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(action, `${kind}:${name}`);
  return ts.transpileModule(`globalThis.action = ${action.getText(ast)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
}

function fixture(kind, name) {
  let state = { messages: [], isStreaming: false, streamingClientMsgNo: '', streamCanceling: false };
  const timers = [];
  const set = patch => { state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }; };
  const actions = {
    fetchStaffInfo: async () => {}, incrementUnreadCount() {}, finalizeStreamMessage() {},
    markStreamingStart: id => set({ isStreaming: true, streamingClientMsgNo: id }),
  };
  const self = { get _state() { return state; }, _setState: set, ...actions };
  const scope = {
    get: () => ({ ...state, ...actions }), set, self, uidForIM: 'visitor-vtr', console: { log() {}, warn() {}, error() {} },
    toPayloadFromAny: value => value, types: { toPayloadFromAny: value => value },
    mergeHistoryMessages: (old, incoming) => [...old, ...incoming],
    playNotificationSound() {}, showBrowserNotification: async () => {},
    usePlatformStore: { getState: () => ({ config: {} }) }, i18n: { t: key => key },
    activeParsers: kind === 'widget' ? new Map() : {},
    createMixedStreamParser: () => ({ push() {}, flush() {} }),
    jsonRenderUtils: { createMixedStreamParser: () => ({ push() {}, flush() {} }) },
    streamTimer: 'timer', clearTimeout: timer => timers.push(timer),
  };
  vm.runInNewContext(implementation(kind, 'markStreamingEnd'), scope);
  const endAction = scope.action;
  // Keep the real end implementation bound when loading the actual listener.
  actions.markStreamingEnd = id => endAction.call(self, id);
  self.markStreamingEnd = actions.markStreamingEnd;
  vm.runInNewContext(implementation(kind, name), scope);
  return { state: () => state, set, timers, call: value => scope.action.call(self, value) };
}

function anchor(extra = {}) {
  return { messageId: 'anchor', clientMsgNo: 'reply-1', fromUid: 'operator-staff',
    timestamp: 1, channelId: 'visitor-vtr', channelType: 251, payload: { type: 100 }, ...extra };
}

for (const kind of ['widget', 'mini']) {
  test(`${kind}: stream anchor exposes stop before the first text chunk`, () => {
    const f = fixture(kind, 'onMessage');
    f.call(anchor());
    assert.equal(f.state().isStreaming, true);
    assert.equal(f.state().streamingClientMsgNo, 'reply-1');
  });
  test(`${kind}: ordinary messages and self echoes never start a reply`, () => {
    for (const extra of [{ payload: { type: 1, content: 'reply' } }, { fromUid: 'visitor-vtr' }]) {
      const f = fixture(kind, 'onMessage');
      f.call(anchor(extra));
      assert.equal(f.state().isStreaming, false);
    }
  });
  test(`${kind}: stale anchors never replace a completed answer`, () => {
    const f = fixture(kind, 'onMessage');
    f.set({ messages: [{ id: 'final', clientMsgNo: 'reply-1', payload: { type: 1, content: 'done' } }] });
    f.call(anchor());
    assert.equal(f.state().isStreaming, false);
    assert.equal(f.state().messages.length, 1);
    assert.equal(f.state().messages[0].payload.content, 'done');
  });
  test(`${kind}: buffered deltas start reply state even without a newline`, () => {
    const f = fixture(kind, 'onCustom');
    f.call({ type: 'stream.delta', data: { client_msg_no: 'reply-1', payload: { delta: 'partial' } } });
    assert.equal(f.state().isStreaming, true);
    assert.equal(f.state().streamingClientMsgNo, 'reply-1');
  });
  for (const event of ['stream.close', 'stream.error', 'stream.cancel', '___TextMessageEnd']) {
    test(`${kind}: late ${event} cannot stop a newer reply`, () => {
      const f = fixture(kind, 'onCustom');
      f.set({ isStreaming: true, streamingClientMsgNo: 'newer' });
      f.call({ type: event, id: 'older', data: { client_msg_no: 'older', payload: {} } });
      assert.equal(f.state().isStreaming, true);
      assert.equal(f.state().streamingClientMsgNo, 'newer');
      assert.equal(f.timers.length, 0);
    });
  }
  test(`${kind}: matching completion clears its own state and timeout`, () => {
    const f = fixture(kind, 'onCustom');
    f.set({ isStreaming: true, streamingClientMsgNo: 'reply-1' });
    f.call({ type: 'stream.close', data: { client_msg_no: 'reply-1', payload: {} } });
    assert.equal(f.state().isStreaming, false);
    assert.equal(f.timers.length, 1);
  });
}
