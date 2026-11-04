import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8')
const tree = ts.createSourceFile('chatStore.ts', source, ts.ScriptTarget.Latest, true)
const names = new Set(['tryParsePayloadString', 'toPayloadFromAny', 'mapHistoryToChatMessage'])
const declarations = tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
assert.equal(declarations.length, names.size)
const compiled = ts.transpileModule(declarations.map(node => node.getText(tree)).join('\n'), {
  compilerOptions: {target: ts.ScriptTarget.ES2022},
}).outputText
const context = {
  atob, Date, JSON, console,
  isSystemMessageType: type => type >= 1000 && type <= 2000,
  createMixedStreamParser: ({onText}) => ({push(value) {onText(value)}, flush() {}}),
}
vm.runInNewContext(compiled, context)

test('web AI stream origin survives both the live anchor and a history snapshot', () => {
  const live = context.toPayloadFromAny({type: 100, content: '', sender_kind: 'ai'})
  assert.equal(live.sender_kind, 'ai')
  const history = context.mapHistoryToChatMessage({
    message_id_str: 'message-1', from_uid: 'staff-1-staff',
    payload: {type: 100, content: '', sender_kind: 'ai'},
    event_meta: {has_events: true, events: [{event_key: 'main', snapshot: {kind: 'text', text: 'AI 答复'}}]},
    timestamp: 1,
  }, 'visitor-1-vtr')
  assert.equal(history.senderKind, 'ai')
  assert.match(history.payload.content, /AI 答复/)
})

test('web staff text and untrusted sender labels are not shown as AI', () => {
  const staff = context.mapHistoryToChatMessage({
    message_id_str: 'message-2', from_uid: 'staff-1-staff',
    payload: {type: 1, content: '人工答复', sender_kind: 'ai'}, timestamp: 2,
  }, 'visitor-1-vtr')
  assert.equal(staff.senderKind, undefined)
  assert.equal(context.toPayloadFromAny({type: 100, sender_kind: 'staff'}).sender_kind, undefined)
})

test('web encoded AI anchor keeps its label when history is restored', () => {
  const payload = Buffer.from(JSON.stringify({type: 100, sender_kind: 'ai'})).toString('base64')
  const mapped = context.mapHistoryToChatMessage({
    message_id_str: 'message-3', from_uid: 'staff-1-staff', payload, timestamp: 3,
    event_meta: {has_events: true, events: [{event_key: 'main', snapshot: {kind: 'text', text: '历史回答'}}]},
  }, 'visitor-1-vtr')
  assert.equal(mapped.senderKind, 'ai')
})
