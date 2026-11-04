import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const ids = { visitorId: '10000000-0000-0000-0000-000000000001', projectId: '10000000-0000-0000-0000-000000000002',
  platformId: '10000000-0000-0000-0000-000000000003' }
const session = { connection_id: '20000000-0000-0000-0000-000000000001', customer_session: 'private-session-fixture-do-not-log' }

function module(extra = {}) {
  const source = readFileSync(new URL('../src/services/businessCustomerSession.ts', import.meta.url), 'utf8')
  const exports = {}
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, require: () => ({}), URL, Map, Promise, Error, Object, Array, JSON, Date, setTimeout, clearTimeout, ...extra })
  return exports
}

function browser() {
  const sent = [], listeners = []
  const parent = { postMessage: (...args) => sent.push(args) }
  const win = { parent, location: { href: 'https://widget.test/index.html?businessIdentity=1&hostOrigin=https%3A%2F%2Fshop.test' },
    crypto: { randomUUID: () => '30000000-0000-0000-0000-000000000001' },
    addEventListener: (kind, listener) => listeners.push(listener), removeEventListener: (kind, listener) => listeners.splice(listeners.indexOf(listener), 1) }
  const emit = (data, origin = 'https://shop.test', source = parent) => listeners.forEach(listener => listener({ data, origin, source }))
  return { win, parent, sent, emit, listeners }
}

test('trusted provider change invalidates an early lifecycle snapshot and notifies its owner', async () => {
  const f=browser(), lib=module()
  const bridge=new lib.BusinessCustomerSessionBridge(f.win,'https://shop.test/page')
  let changes=0
  const off=bridge.subscribe(()=>changes++)
  f.emit({type:'TGO_BUSINESS_SESSION_STATE',enabled:true,generation:1})
  const captured=await bridge.snapshot()
  assert.equal(captured.hasProvider,true)
  f.emit({type:'TGO_BUSINESS_SESSION_STATE',enabled:false,generation:2},'https://other.test')
  captured.assertCurrent();assert.equal(changes,0)
  f.emit({type:'TGO_BUSINESS_SESSION_STATE',enabled:false,generation:2})
  assert.throws(()=>captured.assertCurrent())
  assert.equal(changes,1)
  off();bridge.dispose();assert.equal(changes,1)
})

test('an early snapshot cannot silently adopt a provider changed before its microtask resumes',async()=>{
  const f=browser(),lib=module(),bridge=new lib.BusinessCustomerSessionBridge(f.win,'https://shop.test/page')
  f.emit({type:'TGO_BUSINESS_SESSION_STATE',enabled:true,generation:1})
  const state=bridge.snapshot()
  f.emit({type:'TGO_BUSINESS_SESSION_STATE',enabled:true,generation:2})
  await assert.rejects(state)
  bridge.dispose()
})

test('signed input accepts only bounded unique connection/token pairs and clones them', () => {
  const lib = module()
  const values = [session]
  const result = lib.normalizeCustomerSessions(values)
  assert.deepEqual(JSON.parse(JSON.stringify(result)), values)
  assert.notEqual(result[0], values[0])
  for (const bad of [null, [session, session], Array(21).fill(session), [{ ...session, customer_id: 'self-claimed' }],
    [{ ...session, customer_session: '' }], [{ ...session, connection_id: 'not-a-uuid' }], [{ ...session, customer_session: 'x'.repeat(8193) }]]) {
    assert.throws(() => lib.normalizeCustomerSessions(bad), error => !error.message.includes(session.customer_session))
  }
})

test('iframe request and response pin parent source/origin and exact request identity', async () => {
  const f = browser(), lib = module()
  const bridge = new lib.BusinessCustomerSessionBridge(f.win, 'https://shop.test/page')
  f.emit({ type: 'TGO_BUSINESS_SESSION_STATE', enabled: true, generation: 0 })
  const promise = bridge.request(ids)
  await new Promise(resolve => setImmediate(resolve))
  const request = f.sent.find(call => call[0].type === 'TGO_BUSINESS_SESSION_REQUEST')[0]
  assert.equal(f.sent.every(call => call[1] === 'https://shop.test'), true)
  let done = false; promise.then(() => { done = true })
  const response = { type: 'TGO_BUSINESS_SESSION_RESPONSE', requestId: request.requestId, context: ids, generation: 0, ok: true, sessions: [session] }
  f.emit(response, 'https://other.test'); f.emit(response, 'https://shop.test', {})
  f.emit({ ...response, requestId: 'unknown-request' }); f.emit({ ...response, context: { ...ids, visitorId: 'foreign-visitor' } })
  await Promise.resolve(); assert.equal(done, false)
  f.emit(response)
  assert.deepEqual(JSON.parse(JSON.stringify(await promise)), [session])
  assert.equal(JSON.stringify(f.sent).includes(session.customer_session), false)
  bridge.dispose(); assert.equal(f.listeners.length, 0)
})

test('provider generation change cancels pending request without retaining a token', async () => {
  const f = browser(), lib = module()
  const bridge = new lib.BusinessCustomerSessionBridge(f.win, 'https://shop.test/page')
  f.emit({ type: 'TGO_BUSINESS_SESSION_STATE', enabled: true, generation: 0 })
  const result = bridge.request(ids)
  f.emit({ type: 'TGO_BUSINESS_SESSION_STATE', enabled: false, generation: 1 })
  await assert.rejects(result, error => !error.message.includes(session.customer_session))
  assert.equal(await bridge.request(ids), undefined)
  bridge.dispose()
})

test('ordinary standalone chat never requests or stores business credentials', async () => {
  const f = browser(), lib = module()
  f.win.parent = f.win
  const bridge = new lib.BusinessCustomerSessionBridge(f.win, '')
  assert.equal(await bridge.request(ids), undefined)
  assert.equal(f.sent.length, 0)
  bridge.dispose()
})

test('initialized provider cannot silently be skipped before the first host state arrives', async () => {
  const f = browser(), lib = module()
  f.win.location.href = 'https://widget.test/index.html?hostOrigin=https%3A%2F%2Fshop.test'
  const bridge = new lib.BusinessCustomerSessionBridge(f.win, 'https://shop.test/page')
  const pending = bridge.request(ids)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.sent.some(call => call[0].type === 'TGO_BUSINESS_SESSION_REQUEST'), false)
  f.emit({ type: 'TGO_BUSINESS_SESSION_STATE', enabled: true, generation: 3 })
  await new Promise(resolve => setImmediate(resolve))
  const request = f.sent.find(call => call[0].type === 'TGO_BUSINESS_SESSION_REQUEST')[0]
  f.emit({ type: 'TGO_BUSINESS_SESSION_RESPONSE', requestId: request.requestId, context: ids, generation: 3, ok: true, sessions: [session] })
  assert.equal((await pending)[0].customer_session, session.customer_session)
  bridge.dispose()
})

test('a resolved credential lease stops being usable after provider reset', async () => {
  const f = browser(), lib = module({ window: f.win, document: { referrer: 'https://shop.test/page' } })
  const pending = lib.acquireBusinessCustomerSessions(ids)
  f.emit({ type: 'TGO_BUSINESS_SESSION_STATE', enabled: true, generation: 0 })
  await new Promise(resolve => setImmediate(resolve))
  const request = f.sent.find(call => call[0].type === 'TGO_BUSINESS_SESSION_REQUEST')[0]
  f.emit({ type: 'TGO_BUSINESS_SESSION_RESPONSE', requestId: request.requestId, context: ids, generation: 0, ok: true, sessions: [session] })
  const lease = await pending
  lease.assertCurrent()
  f.emit({ type: 'TGO_BUSINESS_SESSION_STATE', enabled: false, generation: 1 })
  assert.throws(() => lease.assertCurrent())
  lib.disposeBusinessCustomerSessions()
})
