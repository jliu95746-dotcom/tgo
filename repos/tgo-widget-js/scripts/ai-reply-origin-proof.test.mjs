import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const scope = { exports: {}, Set, JSON }
vm.runInNewContext(ts.transpileModule(readFileSync(
  new URL('../src/utils/aiReplyOrigin.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, scope)
const { isVerifiedAIReply, originKey } = scope.exports

test('visitor AI label requires verified evidence for its current message and channel', () => {
  const candidate = {
    role: 'agent', senderKind: 'ai', channelId: 'visitor-vtr',
    channelType: 251, clientMsgNo: 'ai_123',
  }
  const proof = new Set([originKey('visitor-vtr', 'ai_123')])
  assert.equal(isVerifiedAIReply(candidate, 'visitor-vtr', new Set()), false)
  assert.equal(isVerifiedAIReply(candidate, 'another-vtr', proof), false)
  assert.equal(isVerifiedAIReply({ ...candidate, clientMsgNo: 'other' }, 'visitor-vtr', proof), false)
  assert.equal(isVerifiedAIReply({ ...candidate, senderKind: undefined }, 'visitor-vtr', proof), false)
  assert.equal(isVerifiedAIReply({ ...candidate, role: 'user' }, 'visitor-vtr', proof), false)
  assert.equal(isVerifiedAIReply(candidate, 'visitor-vtr', proof), true)
})
