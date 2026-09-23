import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8')
const tree = ts.createSourceFile('chatStore.ts', source, ts.ScriptTarget.Latest, true)
const actions = []
function visit(node) {
  if (ts.isPropertyAssignment(node) && ['sendMessage', 'retryMessage'].includes(node.name.getText(tree))) actions.push(node.getText(tree))
  ts.forEachChild(node, visit)
}
visit(tree)
assert.equal(actions.length, 2)
const compiled = ts.transpileModule(`exports.actions = {${actions.join(',')}}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

function fixture({ reasonCode = 1, completionError = false, httpError = false } = {}) {
  let state = { messages: [], apiBase: 'http://owned.test', myUid: 'visitor', channelId: 'channel', channelType: 251 }
  const calls = [], retries = [], exports = {}
  vm.runInNewContext(compiled, {
    exports, Date, Math, crypto: { randomUUID: () => 'owned-source' },
    console: { log() {}, warn() {}, error() {} },
    get: () => state,
    set: update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) } },
    resolveApiKey: () => 'owned-key', ReasonCode: { Success: 1, Unknown: 0 },
    IMService: { isReady: true, sendText: async () => ({ reasonCode }),
      sendPayload: async (...args) => { retries.push(args); return { reasonCode } } },
    // Background reconciliation should not run during this bounded unit test.
    setTimeout() {},
    fetch: async (_, options) => {
      calls.push(JSON.parse(options.body))
      if (completionError) throw new TypeError('Failed to fetch')
      return { ok: !httpError, json: async () => httpError ? { event_type: 'error', message: 'Unavailable' } : { event_type: 'accepted' } }
    },
  })
  return { run: exports.actions.sendMessage, retry: exports.actions.retryMessage, state: () => state, calls, retries }
}

for (const failure of [{ completionError: true }, { httpError: true }]) {
  test(`confirmed delivery survives completion ${Object.keys(failure)[0]}`, async () => {
    const f = fixture(failure)
    await f.run('owned text')
    assert.equal(f.state().messages[0].reasonCode, 1)
    assert.equal(f.state().messages[0].status, undefined)
    assert.equal(f.state().messages[0].replyRequestFailed, true)
    assert.equal(f.calls[0].forward_user_message_to_wukongim, false)
    assert.equal(f.calls[0].source_message_id, 'owned-source')
  })
}

test('rejected delivery never starts a completion request', async () => {
  const f = fixture({ reasonCode: 0 })
  await f.run('owned text')
  assert.equal(f.calls.length, 0)
  assert.equal(f.state().messages[0].reasonCode, 0)
  assert.notEqual(f.state().messages[0].replyRequestFailed, true)
})

test('successful completion leaves no failure notice', async () => {
  const f = fixture()
  await f.run('owned text')
  assert.equal(f.state().messages[0].reasonCode, 1)
  assert.notEqual(f.state().messages[0].replyRequestFailed, true)
})

test('a stale retry action cannot resend confirmed text', async () => {
  const f = fixture({ completionError: true })
  await f.run('owned text')
  await f.retry(f.state().messages[0].id)
  assert.equal(f.retries.length, 0)
  assert.equal(f.state().messages[0].reasonCode, 1)
})
