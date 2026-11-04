const assert = require('node:assert/strict')
const test = require('node:test')

const metadata = { visitorId: '10000000-0000-0000-0000-000000000001', projectId: '10000000-0000-0000-0000-000000000002', platformId: '10000000-0000-0000-0000-000000000003' }
const sessions = [{ connection_id: '20000000-0000-0000-0000-000000000001', customer_session: 'private-session-only-fixture' }]

test('mini provider uses current metadata and returns cloned private sessions only to caller', async () => {
  const { createProvider } = require('../src/services/businessCustomerSession')
  const provider = createProvider()
  let input
  provider.set(function (value) { input = value; return Promise.resolve(sessions) })
  const lease = await provider.acquire(metadata)
  assert.deepEqual(input, metadata)
  assert.deepEqual(lease.sessions, sessions)
  assert.notEqual(lease.sessions[0], sessions[0])
  lease.assertCurrent()
  assert.equal(JSON.stringify(provider).includes(sessions[0].customer_session), false)
})

test('changing provider during acquisition or after acquisition invalidates old lease', async () => {
  const { createProvider } = require('../src/services/businessCustomerSession')
  const provider = createProvider()
  let finish
  provider.set(function () { return new Promise(function (resolve) { finish = resolve }) })
  const pending = provider.acquire(metadata)
  await new Promise(resolve => setImmediate(resolve))
  provider.set(null)
  finish(sessions)
  await assert.rejects(pending)
  provider.set(function () { return Promise.resolve(sessions) })
  const lease = await provider.acquire(metadata)
  provider.invalidate()
  assert.throws(function () { lease.assertCurrent() })
})

test('old component release cannot remove the newer provider', async () => {
  const { createProvider } = require('../src/services/businessCustomerSession')
  const provider = createProvider()
  const oldRelease = provider.set(function () { return Promise.resolve([]) })
  provider.set(function () { return Promise.resolve(sessions) })
  oldRelease()
  assert.deepEqual((await provider.acquire(metadata)).sessions, sessions)
})

test('malformed sessions and callback errors do not expose raw tokens', async () => {
  const { createProvider } = require('../src/services/businessCustomerSession')
  for (const value of [[...sessions, ...sessions], [{ ...sessions[0], customer_id: 'self-claimed' }], null]) {
    const provider = createProvider()
    provider.set(function () { return Promise.resolve(value) })
    await assert.rejects(provider.acquire(metadata), error => !error.message.includes(sessions[0].customer_session))
  }
})
