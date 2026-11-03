var assert = require('node:assert/strict')
var test = require('node:test')
var vm = require('node:vm')
var fs = require('node:fs')
var path = require('node:path')

function fixture(fail,persist=true,remove=true) {
  var cached = {visitor_id:'owned-visitor',visitor_access_token:'old-readable-secret',im_token:'old-readable-im',visitor_end_token:'end-only-proof'}
  var memory = {}, calls = [], output = {exports:{}}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/services/visitorLifecycle.js'),'utf8'),{
    module:output,require:function (name) {
      if (name==='./visitor') return {loadCachedVisitor:function () {return cached},removeCachedVisitor:function () {if(remove)cached=null}}
      if (name.indexOf('/storage')!==-1) return {getJSON:function (key) {return memory[key] || null},setJSON:function (key,value) {if(persist)memory[key]=value}}
      return {request:function (request) {
        calls.push(request)
        assert.equal(cached,null)
        assert.equal(JSON.stringify(memory).indexOf('old-readable'),-1)
        if (fail) return Promise.reject(new Error('private-upstream-error'))
        return Promise.resolve({ok:true,json:function () {return Promise.resolve({status:'ended',upstream_stop_status:'pending',im_disconnect_status:'requested'})}})
      }}
    },
  })
  return {service:output.exports,calls:calls,memory:memory}
}
test('mini end clears readable cache before request and only retains an end grant',async function () {
  var f=fixture(false)
  var result=await f.service.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public'})
  assert.equal(result.accessEnded,true)
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].body)),{platform_api_key:'public',visitor_id:'owned-visitor',visitor_end_token:'end-only-proof'})
  assert.equal(result.upstreamStopStatus,'pending')
})
test('mini unknown end remains retryable without read/IM secrets or raw errors',async function () {
  var f=fixture(true)
  var result=await f.service.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public'})
  assert.equal(result.accessEnded,false)
  assert.equal(JSON.stringify(result).indexOf('private-upstream'),-1)
  await f.service.retryPendingVisitorEnds({apiBase:'http://owned.test',platformApiKey:'public'})
  assert.equal(f.calls.length,2)
  assert.equal(f.calls[1].body.visitor_end_token,'end-only-proof')
})

test('silent storage retention failures prevent an end request or an apparent success',async function () {
  var missingLedger=fixture(false,false)
  await assert.rejects(missingLedger.service.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public'}),/无法保存/)
  assert.equal(missingLedger.calls.length,0)
  var readableStillPresent=fixture(false,true,false)
  await assert.rejects(readableStillPresent.service.endCachedVisitorAccess({apiBase:'http://owned.test',platformApiKey:'public'}),/无法清理/)
  assert.equal(readableStillPresent.calls.length,0)
})
