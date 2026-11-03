import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const context={visitorId:'10000000-0000-0000-0000-000000000001',projectId:'10000000-0000-0000-0000-000000000002',platformId:'10000000-0000-0000-0000-000000000003'}
const identity={connection_id:'10000000-0000-0000-0000-000000000004',merchant_assertion:'private-signed-merchant-fixture'}
function fixture({echo=false,changed=false,reason}={}) {
  const exports={},calls=[]
  let checks=0
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/businessCustomerSession.ts',import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
    exports,AbortController,setTimeout,clearTimeout,fetch:async(url,options)=>{
      calls.push({url,body:JSON.parse(options.body)})
      if(reason) return {ok:false,status:409,text:async()=>JSON.stringify({error:{details:{reason}}})}
      return {ok:true,text:async()=>JSON.stringify({visitor_id:context.visitorId,project_id:context.projectId,platform_id:context.platformId,
        connection_id:identity.connection_id,can_enable:false,session_token:echo?identity.merchant_assertion:'resolved-private-session'})}
    }
  })
  return {exports,calls,assertCurrent:()=>{if(changed&&++checks>1)throw new Error('changed')}}
}

test('signed assertion exchange carries viewer possession only to platform and returns normalized session',async()=>{
  const f=fixture()
  const result=await f.exports.resolveCustomerIdentities([identity],context,{apiBase:'https://owned-api.test',visitorAccessToken:'private-browser-access'},f.assertCurrent)
  assert.deepEqual(JSON.parse(JSON.stringify(result)),[{connection_id:identity.connection_id,customer_session:'resolved-private-session'}])
  assert.equal(f.calls[0].body.visitor_access_token,'private-browser-access')
  assert.equal(f.calls[0].body.merchant_assertion,identity.merchant_assertion)
  assert.equal(f.calls[0].url.includes('private-browser-access'),false)
})

test('only the verified owner-change reason permits a fresh lifecycle; nonce conflicts stay rejected',async()=>{
  for(const reason of ['visitor_owner_changed','nonce_replayed']){
    const f=fixture({reason})
    await assert.rejects(f.exports.resolveCustomerIdentities([identity],context,{apiBase:'https://owned-api.test',visitorAccessToken:'private-browser-access'},f.assertCurrent),
      error => reason==='visitor_owner_changed' ? error instanceof f.exports.CustomerIdentityChangedError : !(error instanceof f.exports.CustomerIdentityChangedError))
  }
})

test('unowned assertion, echoed secret and changed context cannot resolve into a session',async()=>{
  const missing=fixture()
  await assert.rejects(missing.exports.resolveCustomerIdentities([identity],context,undefined,missing.assertCurrent))
  assert.equal(missing.calls.length,0)
  for(const options of [{echo:true},{changed:true}]) {
    const f=fixture(options)
    await assert.rejects(f.exports.resolveCustomerIdentities([identity],context,{apiBase:'https://owned-api.test',visitorAccessToken:'private-browser-access'},f.assertCurrent),error=>!error.message.includes(identity.merchant_assertion))
  }
})
