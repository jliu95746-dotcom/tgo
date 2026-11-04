import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../public/tgo-widget-sdk.js', import.meta.url), 'utf8')
const tree = ts.createSourceFile('sdk.js', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
let definition
function find(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === 'createBusinessSessionHost') definition = node.getText(tree)
  ts.forEachChild(node, find)
}
find(tree)
assert.ok(definition)
const context = { visitorId: '10000000-0000-0000-0000-000000000001', projectId: '10000000-0000-0000-0000-000000000002',
  platformId: '10000000-0000-0000-0000-000000000003' }
const sessions = [{ connection_id: '20000000-0000-0000-0000-000000000001', customer_session: 'private-session-fixture-only' }]

function fixture(provider) {
  const exports = {}, posts = []
  vm.runInNewContext(definition + '\nexports.create = createBusinessSessionHost', { exports, AbortController, setTimeout, clearTimeout })
  const controller = { postMessage: (...args) => posts.push(args) }
  const host = exports.create(() => controller, 'https://widget.test', provider)
  const event = { origin: 'https://widget.test', source: controller,
    data: { type: 'TGO_BUSINESS_SESSION_REQUEST', requestId: '30000000-0000-0000-0000-000000000001', context } }
  return { host, event, posts }
}

test('host gives current callback sessions only to the exact iframe and pinned origin', async () => {
  const inputs = []
  const f = fixture(async (value, options) => { inputs.push([value, options]); return sessions })
  await f.host.handle({ ...f.event, source: {} })
  await f.host.handle({ ...f.event, origin: 'https://foreign.test' })
  assert.equal(inputs.length, 0)
  await f.host.handle(f.event)
  assert.equal(inputs.length, 1)
  assert.deepEqual(JSON.parse(JSON.stringify(inputs[0][0])), context)
  assert.equal(inputs[0][1].signal.aborted, false)
  assert.equal(f.posts[0][1], 'https://widget.test')
  assert.deepEqual(JSON.parse(JSON.stringify(f.posts[0][0].sessions)), sessions)
  f.host.dispose()
})

test('provider reset aborts old work and never posts its resolved secret', async () => {
  let finish, signal
  const f = fixture((_, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve }) })
  const original = f.host.handle(f.event)
  await new Promise(resolve => setImmediate(resolve))
  f.host.setProvider(null)
  assert.equal(signal.aborted, true)
  finish(sessions)
  await original
  assert.equal(f.posts.some(call => call[0].type === 'TGO_BUSINESS_SESSION_RESPONSE'), false)
  assert.equal(JSON.stringify(f.posts).includes(sessions[0].customer_session), false)
  f.host.dispose()
})

test('callback error or self-claimed customer field never returns raw details or tokens', async () => {
  for (const provider of [async () => { throw new Error('private-error-with-token') }, async () => [{ ...sessions[0], customer_id: 'self-claimed' }]]) {
    const f = fixture(provider)
    await f.host.handle(f.event)
    assert.equal(f.posts[0][0].ok, false)
    assert.equal(JSON.stringify(f.posts).includes('private-'), false)
    f.host.dispose()
  }
})
