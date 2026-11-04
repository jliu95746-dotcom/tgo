import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import {generationFixture} from './lifecycle-fixture.mjs'
import {loadWebDataAccess} from './visitor-access-fixture.mjs'

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

function fixture({ reasonCode = 1, completionError = false, httpError = false, completionEvent = 'accepted', businessFailure = false } = {}) {
  let state = { messages: [], apiBase: 'http://owned.test', myUid: 'visitor', channelId: 'channel', channelType: 251 }
  const calls = [], retries = [], timers = [], exports = {}
  const sessionExports = {}, senderExports = {}
  const request = async (_, options) => {
    calls.push(JSON.parse(options.body))
    if (completionError) throw new TypeError('Failed to fetch')
    return { ok: !httpError, status: httpError ? 503 : 200,
      json: async () => httpError ? { event_type: 'error', message: 'Unavailable' } : { event_type: completionEvent } }
  }
  for (const [file, target] of [['businessCustomerSession.ts', sessionExports], ['chatCompletion.ts', senderExports]]) {
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/' + file, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText, { exports: target, require: name => name === './visitorDataAccess' ? loadWebDataAccess() : sessionExports, fetch: request })
  }
  vm.runInNewContext(compiled, {
    ...generationFixture(),
    exports, Date, Math, crypto: { randomUUID: () => 'owned-source' },
    console: { log() {}, warn() {}, error() {} },
    get: () => state,
    set: update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) } },
    resolveApiKey: () => 'owned-key', loadCachedVisitor: () => null, ReasonCode: { Success: 1, Unknown: 0 },
    acquireBusinessCustomerSessions: businessFailure
      ? async () => { throw new Error('business identity unavailable') }
      : sessionExports.acquireBusinessCustomerSessions,
    sendTextCompletion: senderExports.sendTextCompletion,
    IMService: { isReady: true, sendText: async () => ({ reasonCode }),
      sendPayload: async (...args) => { retries.push(args); return { reasonCode } } },
    // Background reconciliation should not run during this bounded unit test.
    setTimeout(callback) { timers.push(callback) },
    fetch: request,
  })
  return { run: exports.actions.sendMessage, retry: exports.actions.retryMessage, state: () => state, calls, retries, timers }
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

test('direct human request does not require a merchant business identity', async () => {
  const f = fixture({ businessFailure: true, completionEvent: 'human_handoff' })
  await f.run('转人工')
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].message, '转人工')
  assert.equal(f.calls[0].customer_sessions, undefined)
  assert.notEqual(f.state().messages[0].replyRequestFailed, true)
})

test('human handoff does not schedule an AI reply history poll', async () => {
  const f = fixture({ completionEvent: 'human_handoff' })
  await f.run('转人工')
  assert.equal(f.timers.length, 0)
})

test('a stale retry action cannot resend confirmed text', async () => {
  const f = fixture({ completionError: true })
  await f.run('owned text')
  await f.retry(f.state().messages[0].id)
  assert.equal(f.retries.length, 0)
  assert.equal(f.state().messages[0].reasonCode, 1)
})
