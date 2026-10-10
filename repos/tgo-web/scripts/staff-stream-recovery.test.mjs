import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function action(name, scope) {
  const file = '../src/stores/messageStore.ts';
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  let implementation;
  function visit(node) {
    if (ts.isPropertyAssignment(node) && node.name.getText(ast) === name) implementation = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(implementation, name);
  vm.runInNewContext(ts.transpileModule(`globalThis.action = ${implementation.getText(ast)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, scope);
  return scope.action;
}

test('finish alone ends the matching reply and preserves a concurrent newer reply', () => {
  let state = { messages: [{ clientMsgNo: 'old', content: '回答' }], historicalMessages: {},
    activeStreamingChannels: { old: {}, newer: {} }, isStreamingInProgress: true,
    streamingClientMsgNo: 'newer' };
  const scope = { get: () => state, set: patch => {
    state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
  }, console: { warn() {}, log() {} } };
  state.markStreamMessageEnd = action('markStreamMessageEnd', scope);
  action('markStreamMessageFinish', scope)('old');
  assert.equal(state.messages[0].metadata.stream_end, 1);
  assert.equal(state.messages[0].metadata.is_streaming, false);
  assert.deepEqual(Object.keys(state.activeStreamingChannels), ['newer']);
  assert.equal(state.streamingClientMsgNo, 'newer');
});

test('history recovery starts at an older pending anchor rather than only the latest message', async () => {
  const current = [{ message_seq: 3, payload: { type: 100 }, event_meta: { completed: false } },
    { message_seq: 8, payload: { type: 1 } }];
  let state = { historicalMessages: { conversation: current }, activeStreamingChannels: {} };
  let request;
  const scope = { get: () => state, set: patch => {
    state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
  }, getChannelKey: () => 'conversation', MessagePayloadType: { STREAM: 100 }, console,
  WuKongIMUtils: { extractMessageType: payload => payload.type, mergeMessages: (_, incoming) => incoming },
  WuKongIMApiService: { syncChannelMessages: async input => {
    request = input;
    return { messages: [{ ...current[0], end: 1, event_meta: { completed: true } }, current[1]] };
  } } };
  state.markStreamMessageEnd = () => {};
  await action('loadNewerHistory', scope)('visitor-vtr', 251);
  assert.equal(request.start_message_seq, 3);
  assert.equal(state.historicalMessages.conversation[0].event_meta.completed, true);
});

test('authoritative history ends a missed stream and retains its failure reason', async () => {
  let state = { activeStreamingChannels: { failed: {}, 'still-running': {} },
    historicalMessages: { conversation: [{ message_seq: 3,
    client_msg_no: 'failed', payload: { type: 100 }, event_meta: { completed: false } }] } };
  const ended = [];
  state.markStreamMessageEnd = (...args) => ended.push(args);
  const scope = { get: () => state, set: patch => {
    state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
  }, getChannelKey: () => 'conversation', MessagePayloadType: { STREAM: 100 }, console,
  WuKongIMUtils: { extractMessageType: payload => payload.type, mergeMessages: (_, incoming) => incoming },
  WuKongIMApiService: { syncChannelMessages: async () => ({ messages: [
    { message_seq: 3, client_msg_no: 'failed', payload: { type: 100 }, end: 1,
      error: '已停用', event_meta: { completed: true } },
    { message_seq: 4, client_msg_no: 'still-running', payload: { type: 100 }, end: 0,
      event_meta: { completed: false } },
  ] }) } };
  await action('loadNewerHistory', scope)('visitor-vtr', 251);
  assert.deepEqual(ended, [['failed', '已停用']]);
});

test('pending reply recovery polls without overlapping requests and stops on channel cleanup', async () => {
  const file = '../src/hooks/useHistoricalMessages.ts';
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  let effect;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === 'useEffect'
      && node.arguments[0].getText(ast).includes('hasPendingStream')) effect = node.arguments[0];
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(effect);
  let calls = 0;
  let release;
  let tick;
  let cleared = false;
  const scope = { channelId: 'visitor-vtr', channelType: 251, hasPendingStream: true,
    loadNewerHistory: () => { calls++; return new Promise(resolve => { release = resolve; }); },
    setInterval: callback => { tick = callback; return 42; },
    clearInterval: id => { assert.equal(id, 42); cleared = true; } };
  vm.runInNewContext(ts.transpileModule(`globalThis.effect = ${effect.getText(ast)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, scope);
  const cleanup = scope.effect();
  assert.equal(calls, 1);
  tick();
  assert.equal(calls, 1);
  release();
  await new Promise(resolve => setImmediate(resolve));
  tick();
  assert.equal(calls, 2);
  cleanup();
  assert.ok(cleared);
  release();
  await new Promise(resolve => setImmediate(resolve));
  tick();
  assert.equal(calls, 2);
});
