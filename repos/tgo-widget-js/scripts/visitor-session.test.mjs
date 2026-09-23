import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

function fixture(cached = null, response = {}) {
  const calls = [], writes = [], logs = []
  const exports = {}
  const visitor = { id: 'existing-visitor', platform_open_id: 'existing-open-id',
    channel_id: 'existing-channel', im_token: 'fresh-private-token', ...response }
  const source = readFileSync(new URL('../src/services/visitor.ts', import.meta.url), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, {
    exports, AbortController, setTimeout, clearTimeout,
    require: () => ({ getJSON: () => cached, setJSON: (...args) => writes.push(args) }),
    console: { log: (...args) => logs.push(args), warn: (...args) => logs.push(args) },
    fetch: async (...args) => {
      calls.push(args)
      return { ok: true, json: async () => visitor }
    },
  })
  return { run: exports.refreshVisitorSession, calls, writes, logs, visitor }
}

test('cached identity is retained while expired credentials are refreshed', async () => {
  const f = fixture({ platform_open_id: 'existing-open-id', im_token: 'expired-token' })
  const result = await f.run({ apiBase: 'http://owned.test', platformApiKey: 'owned-key' })
  assert.equal(JSON.parse(f.calls[0][1].body).platform_open_id, 'existing-open-id')
  assert.equal(result.im_token, 'fresh-private-token')
  assert.equal(f.writes[0][1].visitor_id, 'existing-visitor')
  assert.equal(f.writes[0][1].channel_id, 'existing-channel')
  assert.equal(JSON.stringify(f.logs).includes('fresh-priv'), false)
})

test('new visitors register without a fabricated existing identity', async () => {
  const f = fixture()
  await f.run({ apiBase: 'http://owned.test', platformApiKey: 'owned-key', extra: { timezone: 'Asia/Shanghai' } })
  const body = JSON.parse(f.calls[0][1].body)
  assert.equal(body.platform_open_id, undefined)
  assert.equal(body.timezone, 'Asia/Shanghai')
})

test('invalid refreshed credentials never replace the cached session', async () => {
  const f = fixture({ platform_open_id: 'existing-open-id' }, { im_token: undefined })
  await assert.rejects(f.run({ apiBase: 'http://owned.test', platformApiKey: 'owned-key' }), /im_token/)
  assert.equal(f.writes.length, 0)
})

test('missing cached platform identity cannot silently create a new visitor', async () => {
  const f = fixture({ visitor_id: 'existing-visitor', im_token: 'expired-token' })
  await assert.rejects(f.run({ apiBase: 'http://owned.test', platformApiKey: 'owned-key' }), /identity/)
  assert.equal(f.calls.length, 0)
})
