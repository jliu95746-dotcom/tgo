import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

function fixture(cached = null, response = {}, options = {}) {
  const calls = [], writes = [], logs = [], removals = [], contexts = []
  const exports = {}
  const visitor = { id: 'existing-visitor', platform_open_id: 'existing-open-id',
    channel_id: 'existing-channel', im_token: 'fresh-private-token', visitor_access_token: 'fresh-browser-access', ...response }
  const business = {}
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/businessCustomerSession.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports: business, setTimeout, clearTimeout })
  const source = readFileSync(new URL('../src/services/visitor.ts', import.meta.url), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, {
    exports, AbortController, setTimeout, clearTimeout,
    require: (id) => id.includes('businessCustomerSession') ? { ...business,
      captureBusinessCustomerSessionState: async () => ({hasProvider:options.hasProvider ?? !!cached?.business_protected,assertCurrent:()=>{}}),
      acquireBusinessCustomerSessions: async (context) => {
        contexts.push(context)
        if(options.ownerChanged && contexts.length===1) throw new business.CustomerIdentityChangedError()
        return { sessions: options.sessions, assertCurrent: options.assertCurrent || (() => {}) }
      } } : id.includes('visitorLifecycle') ? {
        endCachedVisitorAccess: async () => { removals.push('lifecycle-end'); cached=null },
        endIssuedVisitorAccess: async issued => { removals.push('late-issued-end') },
      } : { getJSON: () => cached, setJSON: (...args) => writes.push(args), remove: (...args) => removals.push(args) },
    console: { log: (...args) => logs.push(args), warn: (...args) => logs.push(args) },
    fetch: async (...args) => {
      calls.push(args)
      options.onFetch?.()
      const result = options.responses?.[calls.length - 1]
      return { ok: !result?.status, status: result?.status || 201,
        text: async () => result?.text || '', json: async () => result?.data || visitor }
    },
  })
  return { run: exports.refreshVisitorSession, calls, writes, logs, visitor, removals, contexts }
}

test('cached identity is retained while expired credentials are refreshed', async () => {
  const f = fixture({ platform_open_id: 'existing-open-id', im_token: 'expired-token' })
  const result = await f.run({ apiBase: 'http://owned.test', platformApiKey: 'owned-key' })
  assert.equal(JSON.parse(f.calls[0][1].body).platform_open_id, 'existing-open-id')
  assert.equal(JSON.parse(f.calls[0][1].body).im_token, 'expired-token')
  assert.equal(result.im_token, 'fresh-private-token')
  assert.equal(f.writes[0][1].visitor_id, 'existing-visitor')
  assert.equal(f.writes[0][1].channel_id, 'existing-channel')
  assert.equal(JSON.stringify(f.logs).includes('fresh-priv'), false)
})

test('late fresh registration cannot write cache or adopt a newer customer provider', async () => {
  let current=true
  const f=fixture(null,{visitor_end_token:'end-only-private'},{onFetch:()=>{current=false}})
  await assert.rejects(f.run({apiBase:'http://owned.test',platformApiKey:'owned-key',assertCurrent:()=>{
    if(!current) throw new Error('changed lifecycle')
  }}),/changed lifecycle/)
  assert.equal(f.writes.length,0)
  assert.equal(f.contexts.length,0)
})

test('a newly enabled business provider ends old anonymous access before asking for any proof',async()=>{
  const f=fixture({visitor_id:'anonymous-old',platform_open_id:'anonymous-open',project_id:'old-project',platform_id:'old-platform',
    visitor_access_token:'old-access',business_protected:false},{},{hasProvider:true})
  await f.run({apiBase:'http://owned.test',platformApiKey:'owned-key'})
  assert.equal(f.removals[0],'lifecycle-end')
  assert.equal(JSON.parse(f.calls[0][1].body).visitor_id,undefined)
  assert.equal(f.contexts.some(value=>value.visitorId==='anonymous-old'),false)
})

test('a verified different owner ends cached access and obtains proof for a distinct new visitor',async()=>{
  const f=fixture({visitor_id:'customer-a',platform_open_id:'a-open',project_id:'a-project',platform_id:'a-platform',
    visitor_access_token:'a-readable',visitor_end_token:'a-end-only',business_protected:true},
    {id:'new-customer-b',project_id:'new-project',platform_id:'new-platform'},
    {ownerChanged:true,sessions:[{connection_id:'40000000-0000-0000-0000-000000000004',customer_session:'b-proof'}]})
  const result=await f.run({apiBase:'http://owned.test',platformApiKey:'owned-key'})
  assert.equal(result.id,'new-customer-b')
  assert.equal(f.removals[0],'lifecycle-end')
  assert.equal(JSON.parse(f.calls[0][1].body).visitor_id,undefined)
  assert.equal(f.contexts[0].visitorId,'customer-a')
  assert.equal(f.contexts[1].visitorId,'new-customer-b')
  assert.equal(f.writes[0][1].visitor_id,'new-customer-b')
})

test('browser access is resumed without retaining raw customer sessions', async () => {
  const session = { connection_id: '40000000-0000-0000-0000-000000000004', customer_session: 'private-customer-proof' }
  const f = fixture({ visitor_id: 'owned-id', platform_open_id: 'owned-open', project_id: 'owned-project',
    platform_id: 'owned-platform', visitor_access_token: 'owned-browser-access', im_token: 'old-im', business_protected: true }, {}, { sessions: [session] })
  await f.run({ apiBase: 'http://owned.test', platformApiKey: 'owned-key' })
  const body = JSON.parse(f.calls[0][1].body)
  assert.equal(body.visitor_access_token, 'owned-browser-access')
  assert.equal(body.im_token, undefined)
  assert.deepEqual(body.customer_sessions, [session])
  assert.deepEqual(JSON.parse(JSON.stringify(f.contexts)), [{visitorId:'owned-id',projectId:'owned-project',platformId:'owned-platform'}])
  assert.equal(JSON.stringify(f.writes).includes(session.customer_session), false)
})

test('an expired ordinary token starts a distinct guest without replaying its public ID', async () => {
  const f = fixture({ platform_open_id: 'unproven-public-id', im_token: 'expired' }, {}, {responses: [{status:401,text:'private-server-error'},
    {data:{id:'new-guest',channel_id:'new-channel',im_token:'new-im',visitor_access_token:'new-access',platform_open_id:'new-open'}}]})
  const result = await f.run({ apiBase:'http://owned.test',platformApiKey:'owned-key' })
  assert.equal(result.id, 'new-guest')
  assert.equal(JSON.parse(f.calls[1][1].body).platform_open_id, undefined)
  assert.equal(f.removals.length, 1)
  assert.equal(JSON.stringify(f.logs).includes('private-server-error'), false)
})

test('protected cached data cannot reconnect without current customer proof', async () => {
  const f = fixture({visitor_id:'old-customer',platform_open_id:'old-open',project_id:'project',platform_id:'platform',business_protected:true})
  await assert.rejects(f.run({apiBase:'http://owned.test',platformApiKey:'owned-key'}), /customer verification/)
  assert.equal(f.calls.length, 0)
  assert.equal(f.removals.length, 1)
  assert.equal(f.writes.length, 0)
})

test('a customer proof echo or changed lease never becomes cached registration', async () => {
  const session = {connection_id:'40000000-0000-0000-0000-000000000004',customer_session:'do-not-echo-customer-proof'}
  const cached = {visitor_id:'v',platform_open_id:'open',project_id:'p',platform_id:'x',business_protected:true}
  const echo = fixture(cached, {name:session.customer_session}, {sessions:[session]})
  await assert.rejects(echo.run({apiBase:'http://owned.test',platformApiKey:'key'}))
  assert.equal(echo.writes.length, 0)
  let checks = 0
  const changed = fixture(cached, {}, {sessions:[session],assertCurrent:()=> {if(++checks > 1) throw new Error('changed customer')}})
  await assert.rejects(changed.run({apiBase:'http://owned.test',platformApiKey:'key'}), /changed customer/)
  assert.equal(changed.writes.length, 0)
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
