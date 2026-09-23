import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/conversationExport.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports, TextEncoder });
const { collectConversationExport } = exports;
const message = seq => ({ message_seq: seq, channel_id: 'own', channel_type: 251, payload: { content: 'synthetic' } });

test('pages backwards, preserves sequence order, and removes duplicate boundary rows', async () => {
  const cursors = [];
  const result = await collectConversationExport('own', 251, async cursor => {
    cursors.push(cursor);
    return cursor === 0 ? { messages: [message(4), message(3)], more: true }
      : { messages: [message(3), message(1)], more: false };
  });
  assert.deepEqual(cursors, [0, 2]);
  assert.deepEqual(Array.from(result, item => item.message_seq), [1, 3, 4]);
});
test('fails closed on inaccessible later page', async () => {
  await assert.rejects(collectConversationExport('own', 251, async cursor => {
    if (cursor) throw new Error('403');
    return { messages: [message(4)], more: true };
  }), /403/);
});
test('rejects foreign channel data and nonprogressing pagination', async () => {
  await assert.rejects(collectConversationExport('own', 251, async () => ({ messages: [{ ...message(4), channel_id: 'foreign' }], more: false })), /EXPORT_INVALID/);
  await assert.rejects(collectConversationExport('own', 251, async () => ({ messages: [message(4)], more: true })), /EXPORT_PAGINATION/);
});
test('rejects overflow instead of returning a truncated export', async () => {
  await assert.rejects(collectConversationExport('own', 251, async () => ({ messages: [message(2), message(1)], more: false }), { maxMessages: 1 }), /EXPORT_LIMIT/);
  await assert.rejects(collectConversationExport('own', 251, async () => ({ messages: [message(1)], more: false }), { maxBytes: 1 }), /EXPORT_LIMIT/);
});
test('cancellation prevents the next page request', async () => {
  let calls = 0;
  await assert.rejects(collectConversationExport('own', 251, async () => { calls++; return { messages: [], more: false }; }, { cancelled: () => true }), /EXPORT_CANCELLED/);
  assert.equal(calls, 0);
});
