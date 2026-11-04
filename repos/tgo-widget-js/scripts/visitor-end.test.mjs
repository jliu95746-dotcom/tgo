import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import test from 'node:test'

const id = '10000000-0000-0000-0000-000000000001'
function fixture(options = {}) {
  let cached = {visitor_id:id,visitor_access_token:'old-readable-access',im_token:'old-readable-im',visitor_end_token:'end-only-fixture-proof'}
  const memory = new Map(), calls = []
  const exports = {}
  const source = readFileSync(new URL('../src/services/visitorLifecycle.ts',import.meta.url),'utf8')
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
    exports, AbortController,setTimeout,clearTimeout,
    require: name => name.endsWith('/visitor') ? {loadCachedVisitor:()=>cached,removeCachedVisitor:()=>{cached=null}} : {
      getJSON:key=>memory.get(key)||null,setJSON:(key,value)=>memory.set(key,value),remove:key=>memory.delete(key),
    },
    fetch:async(url,init)=>{
      calls.push({url,body:JSON.parse(init.body),cache:cached})
      assert.equal(JSON.stringify([...memory.values()]).includes('old-readable'),false)
      if (options.fail) throw new Error('raw-upstream-secret-error')
      return {ok:true,json:async()=>options.response || {status:'ended',upstream_stop_status:'pending',im_disconnect_status:'requested'}}
    },
  })
  return {exports,calls,memory,cache:()=>cached}
}

test('end deletes old readable access before posting only its end grant and keeps pending cleanup',async()=>{
  const f=fixture()
  const result=await f.exports.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public-key'})
  assert.equal(result.accessEnded,true)
  assert.equal(result.upstreamStopStatus,'pending')
  assert.equal(f.cache(),null)
  assert.deepEqual(f.calls[0].body,{platform_api_key:'public-key',visitor_id:id,visitor_end_token:'end-only-fixture-proof'})
  assert.equal(f.calls[0].cache,null)
  assert.equal([...f.memory.values()][0].length,1)
})

test('unknown network result keeps end-only retry, never retains read credentials or reports success',async()=>{
  const f=fixture({fail:true})
  const result=await f.exports.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public-key'})
  assert.equal(result.accessEnded,false)
  assert.equal(f.cache(),null)
  assert.equal(JSON.stringify([...f.memory.values()]).includes('old-readable'),false)
  assert.equal(JSON.stringify(result).includes('raw-upstream-secret'),false)
  await f.exports.retryPendingVisitorEnds({apiBase:'http://owned.test',platformApiKey:'public-key'})
  assert.equal(f.calls.length,2)
  assert.equal(f.calls[1].body.visitor_end_token,'end-only-fixture-proof')
})

test('confirmed original cleanup removes its pending grant, malformed receipts remain pending',async()=>{
  for (const [response,remaining] of [
    [{status:'ended',upstream_stop_status:'confirmed',im_disconnect_status:'requested'},0],
    [{status:'ended',upstream_stop_status:'confirmed',im_disconnect_status:'pending'},1],
    [{status:'ended',upstream_stop_status:'confirmed',im_disconnect_status:'requested',secret:'echoed'},1],
  ]) {
    const f=fixture({response})
    await f.exports.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public-key'})
    assert.equal([...f.memory.values()][0]?.length || 0,remaining)
  }
})
