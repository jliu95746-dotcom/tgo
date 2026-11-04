const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const vm=require('node:vm')
const test=require('node:test')
function fixture(mode) {
  let cache={visitor_id:'owned',channel_id:'owned-vtr',im_token:'private-im-grant'}
  const calls=[]
  function load(file) {
    const module={exports:{}}
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/services',file),'utf8'),{
      module,require:name=>name==='./visitor'?{loadCachedVisitor:()=>cache}:name==='./visitorDataAccess'?load('visitorDataAccess.js'):
        {request:async options=>{calls.push(options);return {ok:true,json:async()=>{
          if(mode==='stale')cache={...cache,visitor_id:'replacement'}
          return {messages:mode==='echo'?[{payload:cache.im_token}]:[]}
        }}}}
    })
    return module.exports
  }
  return {run:load('messageHistory.js').syncVisitorMessages,calls,clear:()=>{cache=null}}
}
const options={apiBase:'http://owned.test',platformApiKey:'public-key',channelId:'owned-vtr',channelType:251}
test('mini history sends the cached signed grant only in the standard header',async()=>{
  const f=fixture();await f.run(options)
  assert.equal(f.calls[0].headers.Authorization,'Bearer private-im-grant')
  assert.equal(f.calls[0].url.includes('private-im-grant'),false)
  assert.equal(JSON.stringify(f.calls[0].body).includes('private-im-grant'),false)
})
test('mini history missing access or a late response cannot release old data',async()=>{
  const empty=fixture();empty.clear();await assert.rejects(empty.run(options));assert.equal(empty.calls.length,0)
  const stale=fixture('stale');await assert.rejects(stale.run(options))
})
test('mini history discards credential echoes without exposing them in errors',async()=>{
  const f=fixture('echo')
  await assert.rejects(f.run(options),error=>!error.message.includes('private-im-grant'))
})
