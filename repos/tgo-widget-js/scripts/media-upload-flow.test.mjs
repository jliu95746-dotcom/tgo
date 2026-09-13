import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8')
const tree = ts.createSourceFile('chatStore.ts', source, ts.ScriptTarget.Latest, true)
const methods = []
function visit(node) {
  if (ts.isPropertyAssignment(node) && ['uploadFiles', 'retryUpload'].includes(node.name.getText(tree))) {
    methods.push(node.getText(tree))
  }
  ts.forEachChild(node, visit)
}
visit(tree)
assert.equal(methods.length, 2)
const compiled = ts.transpileModule(`exports.actions = {${methods.join(',')}}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

function fixture(mime, { delivery = true, replyFailure = false, uploadFailure = false } = {}) {
  let state = { messages: [], apiBase: 'http://owned.test', channelId: 'channel', channelType: 251, myUid: 'visitor' }
  const sends = [], replies = []
  const exports = {}
  const pendingFiles = new Map()
  let failUpload = uploadFailure
  vm.runInNewContext(compiled, {
    exports, AbortController, Date, Math, crypto: { randomUUID: () => 'owned-source' },
    get: () => state,
    set: update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) } },
    pendingFiles, uploadControllers: new Map(),
    readImageDimensions: async () => ({ width: 1, height: 1 }),
    resolveApiKey: () => 'owned-key', i18n: { t: key => key },
    ReasonCode: { Success: 1, Unknown: 0 },
    uploadChatFile: async () => {
      if (failUpload) throw new Error('owned upload failure')
      return { file_id: 'owned-file', file_url: '/v1/chat/files/owned-file', file_type: mime, file_name: 'owned', file_size: 2 }
    },
    IMService: { sendPayload: async (...args) => { sends.push(args); return { reasonCode: delivery ? 1 : 0 } } },
    requestMediaReply: async request => {
      assert.equal(sends.length, 1, 'Request must follow IM acknowledgement')
      if (request.deliveryConfirmed) replies.push(request)
      if (replyFailure) throw new Error('owned reply failure')
    },
  })
  return { actions: exports.actions, sends, replies, pendingFiles, state: () => state,
    recoverUpload: () => { failUpload = false }, file: { type: mime, name: 'owned', size: 2 } }
}

for (const mime of ['image/png', 'audio/wav']) {
  for (const retry of [false, true]) {
    test(`${mime} actual ${retry ? 'retry' : 'upload'} callback starts a reply after confirmation`, async () => {
      const f = fixture(mime, { uploadFailure: retry })
      await f.actions.uploadFiles([f.file])
      await setImmediate()
      if (retry) {
        assert.equal(f.sends.length, 0)
        f.recoverUpload()
        await f.actions.retryUpload(f.state().messages[0].id)
      }
      await setImmediate()
      assert.equal(f.replies.length, 1)
      assert.equal(f.replies[0].sourceMessageId, f.sends[0][1].clientMsgNo)
      assert.equal(f.sends[0][0].file_id, 'owned-file')
      if (mime.startsWith('audio/')) assert.equal(f.sends[0][0].mime_type, mime)
      assert.equal(f.pendingFiles.size, 0)
    })
  }
}

test('failed IM acknowledgement never starts media recognition', async () => {
  const f = fixture('image/png', { delivery: false })
  await f.actions.uploadFiles([f.file])
  await setImmediate()
  assert.equal(f.replies.length, 0)
})

test('reply startup failure preserves the already sent attachment and shows an error', async () => {
  const f = fixture('image/png', { replyFailure: true })
  await f.actions.uploadFiles([f.file])
  await setImmediate()
  assert.equal(f.sends.length, 1)
  assert.equal(f.state().messages[0].reasonCode, 1)
  assert.equal(f.state().messages[0].uploadError, undefined)
  assert.equal(f.state().error, 'errors.mediaReplyFailed')
  assert.equal(f.pendingFiles.size, 0)
})
