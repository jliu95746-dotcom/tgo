import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import test from 'node:test'

function fixture(mode) {
  let cache={visitor_id:'owned',channel_id:'owned-vtr',im_token:'private-im-grant'}
  const calls=[]
  function load(file) {
    const exports={}
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/'+file,import.meta.url),'utf8'),
      {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
      exports,AbortController,setTimeout,clearTimeout,
      require:name=>name==='./visitor'?{loadCachedVisitor:()=>cache}:name==='./visitorDataAccess'?load('visitorDataAccess.ts'):
        {requireApiKeyOrThrow:()=> 'public-key'},
      fetch:async(url,init)=>{
        calls.push({url,init})
        return {ok:true,json:async()=>{if(mode==='stale')cache={...cache,visitor_id:'replacement'};
          return {messages:mode==='echo'?[{payload:cache.im_token}]:[]}}}
      }
    })
    return exports
  }
  const run=load('messageHistory.ts').syncVisitorMessages
  return {run,calls,clear:()=>{cache=null}}
}
const options={apiBase:'http://owned.test',platformApiKey:'public-key',channelId:'owned-vtr',channelType:251}
test('web history carries the cached signed grant only in Authorization',async()=>{
  const f=fixture();await f.run(options)
  assert.equal(f.calls[0].init.headers.Authorization,'Bearer private-im-grant')
  assert.equal(f.calls[0].url.includes('private-im-grant'),false)
  assert.equal(f.calls[0].init.body.includes('private-im-grant'),false)
})
test('web history missing access or a stale response never returns private data',async()=>{
  const empty=fixture();empty.clear();await assert.rejects(empty.run(options));assert.equal(empty.calls.length,0)
  const stale=fixture('stale');await assert.rejects(stale.run(options))
})
test('web history masks a response that echoes the signed grant',async()=>{
  const f=fixture('echo')
  await assert.rejects(f.run(options),error=>!error.message.includes('private-im-grant'))
})
