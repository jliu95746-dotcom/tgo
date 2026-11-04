const assert = require('node:assert/strict')
const test = require('node:test')
const vm = require('node:vm')
const fs = require('node:fs')
const business = require('../src/services/businessCustomerSession')

const sessions = [{connection_id:'40000000-0000-0000-0000-000000000004',customer_session:'private-mini-proof'}]
const cached = {visitor_id:'10000000-0000-0000-0000-000000000001',project_id:'10000000-0000-0000-0000-000000000002',
  platform_id:'10000000-0000-0000-0000-000000000003',platform_open_id:'owned-open',visitor_access_token:'owned-access',im_token:'old-im'}

function fixture(initial, responses = [], persist = true) {
  let stored = initial
  const calls = [], writes = [], removed = [], ends = []
  const module = {exports:{}}
  const response = {id:'new-id',platform_open_id:'new-open',channel_id:'new-channel',im_token:'fresh-im',visitor_access_token:'fresh-access'}
  const adapter = {request: async params => {
    calls.push(params)
    const entry = responses[calls.length - 1]
    return {ok:!entry?.status,status:entry?.status || 201,json:async()=>entry?.data || response,text:async()=>entry?.text || ''}
  }}
  const storage = {getJSON:()=>stored,setJSON:(key,value)=>{writes.push(value);if(persist) stored=value},remove:key=>{removed.push(key);stored=null}}
  const lifecycle = {endCachedVisitorAccess:async options=>{storage.remove('original-cache');return {accessEnded:true}},
    endIssuedVisitorAccess:async (options,issued)=>{ends.push(issued);return {accessEnded:true}}}
  vm.runInNewContext(fs.readFileSync(new URL('../src/services/visitor.js', 'file://' + __filename.replace(/\\/g,'/')), 'utf8'), {
    module,require:name=>name.includes('businessCustomerSession')?business:name.includes('visitorLifecycle')?lifecycle:name.includes('storage')?storage:adapter,
    console:{warn(){}},setTimeout,clearTimeout
  })
  return {run:module.exports.refreshVisitorSession,calls,writes,removed,ends,provider:business.createProvider()}
}

test('mini refresh submits owned credential and customer proof before IM initialization', async () => {
  const f=fixture({...cached,business_protected:true})
  f.provider.set(async()=>sessions)
  await f.run({apiBase:'http://owned.test',platformApiKey:'key',acquireCustomerSessions:context=>f.provider.acquire(context)})
  assert.equal(f.calls[0].body.visitor_access_token,'owned-access')
  assert.equal(f.calls[0].body.im_token,undefined)
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].body.customer_sessions)),sessions)
  assert.equal(JSON.stringify(f.writes).includes(sessions[0].customer_session),false)
})

test('mini expired possession resets the cached ID and registers a distinct anonymous guest', async () => {
  const f=fixture(cached,[{status:401,text:'do-not-echo-private-error'}])
  const result=await f.run({apiBase:'http://owned.test',platformApiKey:'key'})
  assert.equal(result.visitor_id,'new-id')
  assert.equal(f.calls[1].body.platform_open_id,undefined)
  assert.equal(f.removed.length,1)
})

test('mini protected cache with no current customer proof never registers or reconnects', async () => {
  const f=fixture({...cached,business_protected:true})
  await assert.rejects(f.run({apiBase:'http://owned.test',platformApiKey:'key'}),/customer verification/)
  assert.equal(f.calls.length,0)
  assert.equal(f.removed.length,1)
})

test('mini storage failures and proof echoes cannot publish a successful cached identity', async () => {
  const failure=fixture(null,[],false)
  await assert.rejects(failure.run({apiBase:'http://owned.test',platformApiKey:'key'}),/retain visitor/)
  const echo=fixture({...cached,business_protected:true},[{data:{id:'owned',channel_id:'channel',im_token:'im',visitor_access_token:'access',name:sessions[0].customer_session}}])
  echo.provider.set(async()=>sessions)
  await assert.rejects(echo.run({apiBase:'http://owned.test',platformApiKey:'key',acquireCustomerSessions:context=>echo.provider.acquire(context)}))
  assert.equal(echo.writes.length,0)
})

test('mini captures the provider before a fresh request and retires its late result without caching it', async () => {
  let valid=true
  const issued={id:cached.visitor_id,channel_id:'channel',im_token:'old-im',visitor_access_token:'old-readable',visitor_end_token:'end-only'}
  const f=fixture(null,[{data:issued}])
  let providerCaptures=0
  await assert.rejects(f.run({apiBase:'http://owned.test',platformApiKey:'key',
    captureCustomerSessionState:()=>{providerCaptures++;return {hasProvider:true,assertCurrent:()=>{if(!valid)throw new Error('stale')}}},
    assertCurrent:()=>{if(f.calls.length)valid=false}}),/stale/)
  assert.equal(providerCaptures,1)
  assert.equal(f.writes.length,0)
  assert.equal(f.ends.some(v=>v.id===issued.id && v.visitor_end_token==='end-only'),true)
})

test('a provider never learns the previous anonymous visitor identity', async () => {
  const f=fixture(cached)
  let acquired=0
  await f.run({apiBase:'http://owned.test',platformApiKey:'key',
    captureCustomerSessionState:()=>({hasProvider:true,assertCurrent(){}}),
    acquireCustomerSessions:()=>{acquired++;throw new Error('should not receive anonymous history identity')}})
  assert.equal(f.calls[0].body.visitor_id,undefined)
  assert.equal(f.removed.length,1)
  assert.equal(acquired,0)
})

test('only the explicit owner-change reason can create a new visitor after protected proof fails', async () => {
  const f=fixture({...cached,business_protected:true})
  await f.run({apiBase:'http://owned.test',platformApiKey:'key',
    acquireCustomerSessions:()=>Promise.reject(new business.CustomerIdentityChangedError())})
  assert.equal(f.calls.length,1)
  assert.equal(f.calls[0].body.visitor_id,undefined)
  const denied=fixture({...cached,business_protected:true})
  await assert.rejects(denied.run({apiBase:'http://owned.test',platformApiKey:'key',
    acquireCustomerSessions:()=>Promise.reject(new Error('nonce already used'))}),/nonce/)
  assert.equal(denied.calls.length,0)
})
