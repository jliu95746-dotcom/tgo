import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function service(response) {
  const source = readFileSync(new URL('../src/services/aiRunsApi.ts', import.meta.url), 'utf8');
  const scope = { exports: {}, require: name => {
    if (name.includes('BaseApiService')) return { BaseApiService: class { async post() { return response; } } };
    if (name === '@/i18n') return { default: { t: key => key } };
    throw new Error(name);
  } };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, scope);
  return scope.exports.aiRunsApiService;
}

for (const response of [null, {}, { accepted: true, status: 'pending', client_msg_no: 'owned' },
  { accepted: false, status: 'cancelled', client_msg_no: 'owned' },
  { accepted: true, status: 'cancelled', client_msg_no: 'different' }]) {
  test(`unconfirmed cancellation ${JSON.stringify(response)} is rejected`, async () => {
    await assert.rejects(service(response).cancelByClientNo({ client_msg_no: 'owned' }));
  });
}
test('matching confirmed stop is accepted', async () => {
  await service({ accepted: true, status: 'cancelled', client_msg_no: 'owned' }).cancelByClientNo({ client_msg_no: 'owned' });
});

const widgetService = readFileSync(new URL('../../tgo-widget-js/src/services/replyCancellation.ts', import.meta.url), 'utf8');
const miniService = readFileSync(new URL('../../tgo-widget-miniprogram/src/services/chat.js', import.meta.url), 'utf8');
for (const kind of ['widget', 'mini']) {
  for (const reply of [null, {}, { accepted: true, status: 'pending', client_msg_no: 'owned' },
    { accepted: false, status: 'cancelled', client_msg_no: 'owned' },
    { accepted: true, status: 'cancelled', client_msg_no: 'other' },
    { accepted: true, status: 'cancelled', client_msg_no: 'owned' }]) {
    test(`${kind} validates cancellation body ${JSON.stringify(reply)}`, async () => {
      const response = { ok: true, json: async () => reply };
      const scope = { exports: {}, module: { exports: {} }, AbortSignal,
        fetch: async () => response,
        require: () => ({ request: async () => response }) };
      const code = kind === 'widget' ? ts.transpileModule(widgetService, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      }).outputText : miniService;
      vm.runInNewContext(code, scope);
      const cancel = kind === 'widget' ? scope.exports.requestReplyCancellation : scope.module.exports.cancelStreaming;
      const request = cancel({ apiBase: 'http://owned.test', platformApiKey: 'fixture', clientMsgNo: 'owned' });
      if (reply?.accepted === true && reply.status === 'cancelled' && reply.client_msg_no === 'owned') await request;
      else await assert.rejects(request);
    });
  }
}

function propertyAction(path, name) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const ast = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
  let action;
  function visit(node) {
    if (ts.isPropertyAssignment(node) && node.name.getText(ast) === name) action = node.initializer;
    if (ts.isBinaryExpression(node) && node.left.getText(ast) === `ChatStore.prototype.${name}`) action = node.right;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(action, `${path}:${name}`);
  return ts.transpileModule(`globalThis.action = ${action.getText(ast)};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
}

test('staff stop uses latest streaming channels after the response', async () => {
  let finish;
  let state = { streamingClientMsgNo: 'owned', isStreamingInProgress: true,
    activeStreamingChannels: { owned: { channelId: 'a', channelType: 251 } } };
  const scope = { get: () => state, set: patch => { state = { ...state, ...patch }; }, console,
    require: () => ({ aiRunsApiService: { cancelByClientNo: () => new Promise(resolve => { finish = resolve; }) } }) };
  vm.runInNewContext(propertyAction('../src/stores/messageStore.ts', 'cancelStreamingMessage'), scope);
  const pending = scope.action('owned');
  while (!finish) await Promise.resolve();
  state = { ...state, streamingClientMsgNo: 'new', activeStreamingChannels: {
    ...state.activeStreamingChannels, new: { channelId: 'b', channelType: 251 },
  } };
  finish();
  await pending;
  assert.deepEqual(Object.keys(state.activeStreamingChannels), ['new']);
  assert.equal(state.streamingClientMsgNo, 'new');
  assert.equal(state.isStreamingInProgress, true);
});

for (const kind of ['widget', 'mini']) {
  for (const outcome of ['failed', 'confirmed', 'newer']) {
    test(`${kind} ${outcome} stop preserves the correct visible stream`, async () => {
      let finish, fail;
      let state = { apiBase: 'http://owned.test', platformApiKey: 'fixture',
        streamingClientMsgNo: 'owned', streamCanceling: false, isStreaming: true };
      const request = () => new Promise((resolve, reject) => { finish = resolve; fail = reject; });
      const set = patch => { state = { ...state, ...patch }; };
      const end = () => set({ isStreaming: false, streamCanceling: false, streamingClientMsgNo: '' });
      const scope = { console: { warn() {} },
        get: () => ({ ...state, markStreamingEnd: end }), set,
        resolveApiKey: () => 'fixture', requestReplyCancellation: request,
        i18n: { t: key => key }, chatService: { cancelStreaming: request },
      };
      const path = kind === 'widget' ? '../../tgo-widget-js/src/store/chatStore.ts' : '../../tgo-widget-miniprogram/src/core/chatStore.js';
      vm.runInNewContext(propertyAction(path, 'cancelStreaming'), scope);
      const owner = { get _state() { return state; }, _setState: set, markStreamingEnd: end };
      const pending = scope.action.call(owner, 'fixture');
      if (outcome === 'newer') set({ streamingClientMsgNo: 'new', streamCanceling: false });
      if (outcome === 'failed') fail(new Error('HTTP 503')); else finish();
      await pending;
      assert.equal(state.isStreaming, outcome !== 'confirmed');
      assert.equal(state.streamCanceling, false);
      if (outcome === 'newer') assert.equal(state.streamingClientMsgNo, 'new');
      if (outcome === 'failed') assert.ok(state.error);
    });
  }
}
