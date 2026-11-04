const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const business = require('../src/services/businessCustomerSession')
const {loadMiniDataAccess} = require('./visitor-access-fixture.cjs')
const token = 'owned-mini-header-grant'
const params = {apiBase:'http://owned.test',platformApiKey:'public-key',channelId:'channel',channelType:251,
  message:'你好',fromUid:'visitor',sourceMessageId:'source',clientMsgNo:'reply'}

function fixture(name, fault) {
  let cached = fault === 'missing' ? null : {visitor_id:'visitor',channel_id:'channel',im_token:token}
  const calls = [], module = {exports:{}}, access = loadMiniDataAccess(() => cached)
  const adapter = {request:async options => {
    calls.push(options)
    if (fault === 'fetch') cached = {...cached,im_token:'rotated'}
    return {ok:true,status:200,json:async () => {
      if (fault === 'body') cached = {...cached,visitor_id:'other'}
      const value = name === 'cancelStreaming' ? {accepted:true,status:'cancelled',client_msg_no:'reply'} : {event_type:'accepted'}
      return fault === 'echo' ? {...value,message:token} : value
    }}
  }}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/services/chat.js'),'utf8'),{
    module,require:dependency => dependency === './visitorDataAccess' ? access : dependency === './businessCustomerSession' ? business : adapter,
  })
  return {run:module.exports[name],calls}
}
for (const name of ['sendChatCompletion','cancelStreaming']) {
  test(name+' carries its original grant in Authorization only',async () => {
    const f = fixture(name)
    await f.run(params)
    assert.equal(f.calls[0].headers.Authorization,'Bearer '+token)
    assert.equal(JSON.stringify([f.calls[0].url,f.calls[0].body]).includes(token),false)
  })
  for (const fault of ['missing','fetch','body','echo']) test(name+' rejects '+fault,async () => {
    const f = fixture(name,fault)
    await assert.rejects(f.run(params))
    assert.equal(f.calls.length,fault === 'missing' ? 0 : 1)
  })
}
test('foreign chat channel fails before making a request',async () => {
  const f = fixture('sendChatCompletion')
  await assert.rejects(f.run({...params,channelId:'foreign-channel'}))
  assert.equal(f.calls.length,0)
})
