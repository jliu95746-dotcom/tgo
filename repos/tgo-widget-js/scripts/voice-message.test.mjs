import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import test from 'node:test'

const sourceUrl = 'https://untrusted.test/v1/chat/files/11111111-1111-4111-8111-111111111111?file_token=old'
const approvedUrl = 'https://owned.test/v1/chat/files/11111111-1111-4111-8111-111111111111?file_token=short-lived'

function compileFunctions() {
  const text = readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('chatStore.ts', text, ts.ScriptTarget.Latest, true)
  const names = new Set(['tryParsePayloadString', 'toPayloadFromAny', 'mapHistoryToChatMessage'])
  const functions = tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
  assert.equal(functions.length, names.size)
  const compiled = ts.transpileModule(`${functions.map(node => node.getText(tree)).join('\n')}\nexports.parse = toPayloadFromAny; exports.fromHistory = mapHistoryToChatMessage`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const exports = {}
  vm.runInNewContext(compiled, { exports, JSON, Date, atob, console, isSystemMessageType: type => type >= 1000 && type <= 2000 })
  return exports
}

function renderVoice(authorizedUrl) {
  const source = readFileSync(new URL('../src/components/messages/VoiceMessage.tsx', import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const exports = {}
  const requested = []
  vm.runInNewContext(compiled, {
    exports,
    require: key => {
      if (key === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      if (key === 'react-i18next') return { useTranslation: () => ({ t: key => key }) }
      if (key === '../../store/chatFileAccess') return { useChatFileUrls: urls => { requested.push(urls); return [authorizedUrl] } }
      throw new Error(`Unexpected import: ${key}`)
    },
  })
  const tree = exports.default({ url: sourceUrl })
  const audio = JSON.stringify(tree).includes('"type":"audio"') ? tree.props.children.find(child => child?.type === 'audio') : undefined
  return { requested, audio, tree }
}

test('live and history messages retain the same voice payload', () => {
  const { parse, fromHistory } = compileFunctions()
  const payload = { type: 4, content: sourceUrl, url: sourceUrl }
  assert.equal(parse(payload).type, 4)
  assert.equal(parse(JSON.stringify(payload)).url, sourceUrl)
  const historical = fromHistory({
    payload, message_id_str: 'voice-1', message_seq: 1, timestamp: 1,
    from_uid: 'staff', channel_id: 'visitor-vtr', channel_type: 251,
  }, 'visitor-vtr')
  assert.equal(historical.payload.type, 4)
  assert.equal(historical.payload.url, sourceUrl)
})

test('voice player waits for authorized URL and never renders the original private link', () => {
  const denied = renderVoice('')
  assert.deepEqual(JSON.parse(JSON.stringify(denied.requested)), [[sourceUrl]])
  assert.equal(denied.audio, undefined)
  assert.equal(JSON.stringify(denied.tree).includes(sourceUrl), false)
  const allowed = renderVoice(approvedUrl)
  assert.equal(allowed.audio?.props.src, approvedUrl)
  assert.equal(allowed.audio?.props.preload, 'none')
})
