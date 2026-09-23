import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/WebSocketManager.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('WebSocketManager.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'handleVisitorProfileUpdated') callback = node.initializer.arguments[0];
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(callback);
const compiled = ts.transpileModule(`exports.run = ${callback.getText(ast)}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

function fixture({ failure = false } = {}) {
  const exports = {}, calls = [];
  const chatState = {
    applyChannelInfo: () => calls.push('apply'),
    forceSyncConversations: async () => calls.push('sync'),
  };
  vm.runInNewContext(compiled, {
    exports, console: { error() {} },
    useChannelStore: { getState: () => ({
      refreshChannel: async () => { calls.push('authorize'); if (failure) throw new Error('denied'); },
      getChannel: () => ({ name: 'new visitor' }),
    }) },
    useChatStore: { getState: () => chatState },
  });
  return { run: exports.run, calls };
}

test('authorized visitor change refreshes the server conversation list as well as cached profile', async () => {
  const f = fixture();
  await f.run({ channelId: 'new-visitor', channelType: 251 });
  assert.deepEqual(f.calls, ['authorize', 'apply', 'sync']);
});

test('inaccessible visitor event cannot mutate the conversation list', async () => {
  const f = fixture({ failure: true });
  await f.run({ channelId: 'foreign-visitor', channelType: 251 });
  assert.deepEqual(f.calls, ['authorize']);
});

test('invalid visitor event is ignored', async () => {
  const f = fixture();
  await f.run({ channelId: '', channelType: 251 });
  assert.deepEqual(f.calls, []);
});
