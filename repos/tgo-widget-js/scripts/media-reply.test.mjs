import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/services/mediaReply.ts', import.meta.url), 'utf8')
function fixture(status = 200, event = 'accepted') {
  const calls = []
  const exports = {}
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, AbortSignal, fetch: async (...args) => {
    calls.push(args)
    return { ok: status === 200, status, json: async () => ({ event_type: event }) }
  } })
  const request = { apiBase: 'http://owned.test', apiKey: 'owned-fixture', fromUid: 'visitor',
    channelId: 'channel', channelType: 251, sourceMessageId: 'source', deliveryConfirmed: true,
    upload: { file_id: 'file', file_url: '/v1/chat/files/file', file_type: 'image/png' } }
  return { run: exports.requestMediaReply, request, calls }
}

test('confirmed images and audio trigger completion with the same immutable source identity', async () => {
  for (const [mime, type] of [['image/png', 2], ['audio/wav', 4]]) {
    const f = fixture()
    f.request.upload.file_type = mime
    await f.run(f.request)
    const payload = JSON.parse(f.calls[0][1].body)
    assert.equal(payload.source_message_id, 'source')
    assert.equal(payload.media_file_id, 'file')
    assert.equal(payload.msg_type, type)
    assert.equal(payload.forward_user_message_to_wukongim, false)
    assert.equal(payload.wukongim_only, true)
  }
})

test('failed delivery and non-media files never start an AI request', async () => {
  for (const change of [{ deliveryConfirmed: false }, { upload: { file_type: 'application/pdf' } }]) {
    const f = fixture()
    await f.run({ ...f.request, ...change })
    assert.equal(f.calls.length, 0)
  }
})

test('manual and assist acknowledgements are valid without an automatic reply', async () => {
  for (const event of ['assist_mode', 'ai_disabled']) {
    const f = fixture(200, event)
    await f.run(f.request)
    assert.equal(f.calls.length, 1)
  }
})

test('HTTP failure or malformed acknowledgement is not success', async () => {
  for (const [status, event] of [[502, 'error'], [200, 'unknown']]) {
    const f = fixture(status, event)
    await assert.rejects(f.run(f.request))
    assert.equal(f.calls.length, 1)
  }
})
