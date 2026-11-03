const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

function types() {
  const module = {exports: {}}
  const parser = {createMixedStreamParser: ({onText}) => ({push(value) {onText(value)}, flush() {}})}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/core/types.js'), 'utf8'), {
    module, require: () => parser, Date, JSON, console,
    wx: {base64ToArrayBuffer(value) {return Uint8Array.from(Buffer.from(value, 'base64')).buffer}},
  })
  return module.exports
}

test('mini AI anchor remains identified after history replaces its text', () => {
  const mapped = types().mapHistoryToChatMessage({
    message_id_str: 'message-1', from_uid: 'staff-1-staff',
    payload: {type: 100, content: '', sender_kind: 'ai'},
    event_meta: {has_events: true, events: [{event_key: 'main', snapshot: {kind: 'text', text: 'AI 答复'}}]},
    timestamp: 1,
  }, 'visitor-1-vtr')
  assert.equal(mapped.senderKind, 'ai')
  assert.match(mapped.payload.content, /AI 答复/)
})

test('mini staff text and invalid sender labels are not attributed to AI', () => {
  const mapped = types().mapHistoryToChatMessage({
    message_id_str: 'message-2', from_uid: 'staff-1-staff',
    payload: {type: 1, content: '人工答复', sender_kind: 'ai'}, timestamp: 2,
  }, 'visitor-1-vtr')
  assert.equal(mapped.senderKind, undefined)
  assert.equal(types().toPayloadFromAny({type: 100, sender_kind: 'staff'}).sender_kind, undefined)
})

test('mini encoded AI anchor keeps its label when history is restored', () => {
  const payload = Buffer.from(JSON.stringify({type: 100, sender_kind: 'ai'})).toString('base64')
  const mapped = types().mapHistoryToChatMessage({
    message_id_str: 'message-3', from_uid: 'staff-1-staff', payload, timestamp: 3,
    event_meta: {has_events: true, events: [{event_key: 'main', snapshot: {kind: 'text', text: '历史回答'}}]},
  }, 'visitor-1-vtr')
  assert.equal(mapped.senderKind, 'ai')
})
