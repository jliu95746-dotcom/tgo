import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from '../../tgo-widget-js/node_modules/typescript/lib/typescript.js'

const sessions = [{ connection_id: '20000000-0000-0000-0000-000000000001', customer_session: 'private-session-cli-fixture' }]

function fixture({ stream = [], result = { success: true, content: '已发货' }, change = false } = {}) {
  const outputs = [], bodies = [], modules = {}
  let config = {server:'http://owned.test',api_key:'fixture-key',visitor_id:'owned-visitor-id',im_token:'owned-header-grant', platform_open_id: 'owned-visitor', channel_id: 'owned-channel', channel_type: 251 }
  const load = (file, extra = {}) => {
    const exports = {}
    const text = readFileSync(new URL('../src/' + file, import.meta.url), 'utf8')
    vm.runInNewContext(ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
      { exports, require: name => modules[name] || {}, process: { stdout: { write: value => outputs.push(value) }, stderr: { write: value => outputs.push(value) } }, ...extra })
    return exports
  }
  class ApiError extends Error {}
  modules['./client.js'] = { ApiError }
  modules['./businessCustomerSession.js'] = load('businessCustomerSession.ts')
  modules['../businessCustomerSession.js'] = modules['./businessCustomerSession.js']
  modules['../businessChat.js'] = load('businessChat.ts')
  class Client {
    constructor(options) {this.serverUrl=options.server;this.apiKey=options.apiKey}
    async postPublic(path, body) { bodies.push(body); if (change) config = { ...config, platform_open_id: 'different' }; return result }
    async postVisitor(path, body) {return this.postPublic(path,body)}
    async stream(path, body, callbacks) { bodies.push(body); for (const value of stream) callbacks.onMessage(value.event_type, value); callbacks.onClose() }
    async streamVisitor(path,body,callbacks) {return this.stream(path,body,callbacks)}
  }
  const source = readFileSync(new URL('../src/commands/chat.ts', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('chat.ts', source, ts.ScriptTarget.Latest, true)
  const declaration = tree.statements.filter(node => ts.isFunctionDeclaration(node) && ['chatSend','currentVisitorProof'].includes(node.name.text)).map(node => node.getText(tree)).join('\n')
  const exports = {}
  vm.runInNewContext(ts.transpileModule(declaration, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, TgoWidgetClient: Client, normalizeCustomerSessions: modules['./businessCustomerSession.js'].normalizeCustomerSessions,
      sendBusinessChat: modules['../businessChat.js'].sendBusinessChat, loadConfig: () => config,
      resolveServer: () => 'http://owned.test', resolveApiKey: () => 'fixture-key',
      process: { stdout: { write: value => outputs.push(value) }, stderr: { write: value => outputs.push(value) } } })
  return { run: exports.chatSend, outputs, bodies }
}

test('CLI request carries private sessions only in HTTP body and never saves them', async () => {
  const f = fixture()
  const result = await f.run({ message: '查询订单', stream: false, customerSessions: sessions })
  assert.deepEqual(JSON.parse(JSON.stringify(f.bodies[0].customer_sessions)), sessions)
  assert.equal(result.content, '已发货')
  assert.equal(JSON.stringify(f.outputs).includes(sessions[0].customer_session), false)
})

test('private SSE supports current event names and prints only the confirmed final content', async () => {
  const f = fixture({ stream: [
    { event_type: 'agent_content_chunk', data: { data: { content_chunk: '临时草稿' } } },
    { event_type: 'workflow_completed', data: { data: { success: true, final_content: '已发货' } } },
  ] })
  const result = await f.run({ message: '查询订单', customerSessions: sessions })
  assert.equal(result.content, '已发货')
  assert.equal(f.outputs.join(''), '已发货\n')
})

test('unfinished, error and split-token streams produce no private output', async () => {
  for (const stream of [
    [{ event_type: 'agent_content_chunk', data: { content_chunk: '未确认的内容' } }],
    [{ event_type: 'workflow_failed', data: { error_message: 'private-session-cli-fixture' } }],
    [{ event_type: 'agent_content_chunk', data: { content_chunk: 'private-session-' } },
      { event_type: 'agent_content_chunk', data: { content_chunk: 'cli-fixture' } },
      { event_type: 'workflow_completed', data: { success: true, final_content: sessions[0].customer_session } }],
  ]) {
    const f = fixture({ stream })
    await assert.rejects(f.run({ message: '查询订单', customerSessions: sessions }), error => !error.message.includes(sessions[0].customer_session))
    assert.equal(f.outputs.length, 0)
  }
})

test('changed visitor or secret-echo JSON response cannot be printed as current result', async () => {
  for (const options of [{ change: true }, { result: { success: false, error: sessions[0].customer_session } }]) {
    const f = fixture(options)
    await assert.rejects(f.run({ message: '查询订单', stream: false, customerSessions: sessions }), error => !error.message.includes(sessions[0].customer_session))
    assert.equal(f.outputs.length, 0)
  }
})
