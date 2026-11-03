import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/aiReplyOrigin.ts', import.meta.url), 'utf8');
const scope = { exports: {}, Set, JSON };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, scope);
const { isVerifiedAIReply, originKey } = scope.exports;

test('AI badge requires a server-verified message in the active project and channel', () => {
  const message = {
    type: 'staff', senderKind: 'ai', channelId: 'visitor-vtr',
    channelType: 251, clientMsgNo: 'ai_123',
  };
  const empty = { projectId: 'tenant-a', verified: new Set() };
  const verified = { projectId: 'tenant-a', verified: new Set([originKey('visitor-vtr', 'ai_123')]) };

  assert.equal(isVerifiedAIReply(message, 'tenant-a', empty), false);
  assert.equal(isVerifiedAIReply(message, 'tenant-b', verified), false);
  assert.equal(isVerifiedAIReply({ ...message, channelId: 'other-vtr' }, 'tenant-a', verified), false);
  assert.equal(isVerifiedAIReply({ ...message, clientMsgNo: 'other' }, 'tenant-a', verified), false);
  assert.equal(isVerifiedAIReply({ ...message, senderKind: undefined }, 'tenant-a', verified), false);
  assert.equal(isVerifiedAIReply({ ...message, type: 'visitor' }, 'tenant-a', verified), false);
  assert.equal(isVerifiedAIReply({ ...message, channelType: 1 }, 'tenant-a', verified), false);
  assert.equal(isVerifiedAIReply(message, 'tenant-a', verified), true);
});
