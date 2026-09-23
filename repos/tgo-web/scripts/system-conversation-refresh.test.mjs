import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/stores/chatStore.ts', import.meta.url), 'utf8');
const ast = ts.createSourceFile('chatStore.ts', source, ts.ScriptTarget.Latest, true);
let branch;
function visit(node) {
  if (ts.isIfStatement(node) && node.expression.getText(ast).includes('isChannelRefreshSystemMessage(payloadType)')) branch = node;
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(branch);
const compiled = ts.transpileModule(branch.getText(ast), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

async function run(payloadType, { denied = false, syncFails = false } = {}) {
  const calls = [];
  const state = {
    applyChannelInfo: () => calls.push('apply'),
    forceSyncConversations: async () => {
      calls.push('sync');
      if (syncFails) throw new Error('network');
    },
  };
  vm.runInNewContext(compiled, {
    payloadType, channelId: 'new-visitor-vtr', channelType: 251,
    isChannelRefreshSystemMessage: type => [1000, 1001].includes(type),
    console: { log() {}, warn: () => calls.push('handled-error') },
    get: () => state,
    channelStore: { refreshChannel: async () => {
      calls.push('authorize');
      if (denied) throw new Error('forbidden');
      return { extra: { service_status: 'active' } };
    } },
  });
  await new Promise(resolve => setImmediate(resolve));
  return calls;
}

for (const type of [1000, 1001]) {
  test(`system message ${type} refreshes server list even without a profile event`, async () => {
    assert.deepEqual(await run(type), ['authorize', 'apply', 'sync']);
  });
}
test('ordinary text does not fetch the conversation list', async () => {
  assert.deepEqual(await run(1), []);
});
test('denied system channel cannot change list membership', async () => {
  assert.deepEqual(await run(1000, { denied: true }), ['authorize', 'handled-error']);
});
test('list refresh failure is handled without an unhandled rejection', async () => {
  assert.deepEqual(await run(1000, { syncFails: true }), ['authorize', 'apply', 'sync', 'handled-error']);
});
