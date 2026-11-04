const assert=require('node:assert/strict')
const {test}=require('node:test')
const fs=require('node:fs')
const vm=require('node:vm')
const context={visitorId:'10000000-0000-0000-0000-000000000001',projectId:'10000000-0000-0000-0000-000000000002',platformId:'10000000-0000-0000-0000-000000000003'}
const identity={connection_id:'10000000-0000-0000-0000-000000000004',merchant_assertion:'private-mini-signed-assertion'}
function fixture(echo=false,status=200,reason='') {
  const module={exports:{}},calls=[]
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/services/businessCustomerSession'),'utf8'),{module,require:()=>({request:async params=>{
    calls.push(params)
    return{ok:status<400,status,json:async()=> status>=400?{error:{details:{reason}}}:
      ({visitor_id:context.visitorId,project_id:context.projectId,platform_id:context.platformId,
      connection_id:identity.connection_id,can_enable:false,session_token:echo?identity.merchant_assertion:'resolved-mini-session'})}
  }})})
  return{module:module.exports,calls}
}
test('mini signed assertion resolves with owned credential in API body only',async()=>{
  const f=fixture()
  const result=await f.module.resolveCustomerIdentities([identity],context,{apiBase:'https://owned-api.test',visitorAccessToken:'private-mini-access'},()=>{})
  assert.equal(result[0].customer_session,'resolved-mini-session')
  assert.equal(f.calls[0].body.visitor_access_token,'private-mini-access')
  assert.equal(f.calls[0].url.includes('private-mini-access'),false)
})
test('mini signature without holder proof or secret echo cannot resolve',async()=>{
  const missing=fixture()
  await assert.rejects(missing.module.resolveCustomerIdentities([identity],context,undefined,()=>{}))
  assert.equal(missing.calls.length,0)
  const echo=fixture(true)
  await assert.rejects(echo.module.resolveCustomerIdentities([identity],context,{apiBase:'https://owned-api.test',visitorAccessToken:'private-mini-access'},()=>{}))
})

test('only the non-secret explicit 409 owner reason permits registration to reset identity',async()=>{
  for(const [status,reason,changed] of [[409,'visitor_owner_changed',true],[409,'nonce_replayed',false],[401,'visitor_owner_changed',false]]) {
    const f=fixture(false,status,reason)
    await assert.rejects(f.module.resolveCustomerIdentities([identity],context,{apiBase:'http://owned.test',visitorAccessToken:'private-mini-access'},()=>{}),error=>{
      assert.equal(error instanceof f.module.CustomerIdentityChangedError,changed)
      assert.equal(error.message.includes('private-mini-access'),false)
      assert.equal(error.message.includes(reason),false)
      return true
    })
  }
})

test('a stale customer callback cannot turn an owner response into a fresh registration',async()=>{
  const f=fixture(false,409,'visitor_owner_changed')
  await assert.rejects(f.module.resolveCustomerIdentities([identity],context,{apiBase:'http://owned.test',visitorAccessToken:'private-mini-access'},()=>{
    if(f.calls.length)throw new Error('stale identity')
  }),error=>!(error instanceof f.module.CustomerIdentityChangedError))
})
