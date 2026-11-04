import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import {loadWebDataAccess} from './visitor-access-fixture.mjs'

const token = 'owned-header-grant'
const base = {apiBase:'http://owned.test',platformApiKey:'public-key',channelId:'channel',channelType:251,
  message:'你好',fromUid:'visitor',sourceMessageId:'source',clientMsgNo:'reply'}
const variants = [
  ['chatCompletion.ts','sendTextCompletion',base,{event_type:'accepted'}],
  ['replyCancellation.ts','requestReplyCancellation',base,{accepted:true,status:'cancelled',client_msg_no:'reply'}],
  ['mediaReply.ts','requestMediaReply',{...base,apiKey:'public-key',deliveryConfirmed:true,
    upload:{file_id:'file',file_url:'/v1/chat/files/file',file_type:'image/png'}},{event_type:'accepted'}],
  ['aiReplyOrigin.ts','verifyAIReplyOrigins',{...base,clientMsgNos:['reply']},
    [{client_msg_no:'reply',status:'verified'}]],
]

function fixture(file, acknowledgement, fault) {
  let cached = fault === 'missing' ? null : {visitor_id:'visitor',channel_id:'channel',im_token:token}
  const calls = [], exports = {}, access = loadWebDataAccess(() => cached)
  const business = {}
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/businessCustomerSession.ts',import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,{exports:business})
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/'+file,import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,{exports,AbortSignal,require:name => name === './visitorDataAccess' ? access : business,
    fetch:async (...args) => {
      calls.push(args)
      if (fault === 'fetch') cached = {...cached,im_token:'rotated'}
      return {ok:true,status:200,json:async () => {
        if (fault === 'body') cached = {...cached,visitor_id:'other'}
        if (fault === 'echo') return {...acknowledgement,message:token}
        return acknowledgement
      }}
    },
  })
  return {exports,calls}
}

for (const [file,name,params,ack] of variants) {
  test(file+' sends the original grant only in Authorization',async () => {
    const f = fixture(file,ack)
    await f.exports[name](params)
    assert.equal(f.calls[0][1].headers.Authorization,'Bearer '+token)
    assert.equal(JSON.stringify([f.calls[0][0],f.calls[0][1].body]).includes(token),false)
  })
  for (const fault of ['missing','fetch','body','echo']) {
    test(file+' rejects '+fault+' authority before releasing a result',async () => {
      const f = fixture(file,ack,fault)
      await assert.rejects(f.exports[name](params))
      assert.equal(f.calls.length, fault === 'missing' ? 0 : 1)
    })
  }
  if (name !== 'requestReplyCancellation') test(file+' rejects a foreign channel before HTTP',async () => {
    const f = fixture(file,ack)
    await assert.rejects(f.exports[name]({...params,channelId:'other-channel'}))
    assert.equal(f.calls.length,0)
  })
}
