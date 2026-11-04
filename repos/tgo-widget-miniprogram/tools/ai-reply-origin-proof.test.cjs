const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const {loadMiniDataAccess} = require('./visitor-access-fixture.cjs')

function fixture() {
  let definition
  const pending=[]
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/components/message-list/index.js'),'utf8'),{
    Component:value=>{definition=value},setTimeout,clearTimeout,Date,JSON,Promise,Object,
    require:name=>name.includes('aiReplyOrigin')
      ? {verifyAIReplyOrigins:params=>new Promise(resolve=>pending.push({params,resolve}))}
      : name.includes('chatFileAccess')
        ? {isPrivateChatFile:()=>false,maskPrivateChatMarkdown:value=>value||''}
        : name.includes('types')
          ? {isSystemMessageType:()=>false}
          : {formatMessageTime:()=>''}
  })
  const message=(channelId,clientMsgNo)=>({id:clientMsgNo,role:'agent',senderKind:'ai',
    channelId,channelType:251,clientMsgNo,payload:{type:1,content:'AI reply'}})
  const props={messages:[message('visitor-vtr','ai_1')],apiBase:'https://api.test',
    platformApiKey:'public-key',sessionRevision:1}
  const component={properties:props,data:{...definition.data},setData(value){Object.assign(this.data,value)}}
  Object.assign(component,definition.methods)
  definition.lifetimes.attached.call(component)
  return {component,definition,pending,props,message}
}

test('mini AI label appears only after server evidence matches the current reply',async()=>{
  const f=fixture()
  assert.equal(f.component.data.displayMessages[0].senderKind,'')
  assert.equal(f.pending.length,1)
  f.pending[0].resolve([{client_msg_no:'other',status:'verified'}])
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(f.component.data.displayMessages[0].senderKind,'')
  f.definition.lifetimes.detached.call(f.component)
})

test('mini ignores an earlier visitor proof after the session changes',async()=>{
  const f=fixture()
  f.props.sessionRevision=2
  f.props.messages=[f.message('other-vtr','ai_2')]
  f.component._processMessages(f.props.messages)
  assert.equal(f.pending.length,2)
  f.pending[0].resolve([{client_msg_no:'ai_1',status:'verified'}])
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(f.component.data.displayMessages[0].senderKind,'')
  f.pending[1].resolve([{client_msg_no:'ai_2',status:'verified'}])
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(f.component.data.displayMessages[0].senderKind,'ai')
  f.definition.lifetimes.detached.call(f.component)
})

test('mini proof request keeps the visitor grant in Authorization and rejects a changed grant',async()=>{
  let cached={visitor_id:'visitor',channel_id:'visitor-vtr',im_token:'secret-grant'}
  let rotateAfterRequest=false
  const calls=[]
  const module={exports:{}}
  const adapter={request:async options=>{
    calls.push(options)
    if (rotateAfterRequest) cached={...cached,im_token:'rotated'}
    return {ok:true,json:async()=>[{client_msg_no:'ai_1',status:'verified'}]}
  }}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/services/aiReplyOrigin.js'),'utf8'),{
    module,Promise,Array,
    require:name=>name.includes('visitorDataAccess')?loadMiniDataAccess(()=>cached):adapter,
  })
  const params={apiBase:'https://api.test',platformApiKey:'public-key',
    channelId:'visitor-vtr',clientMsgNos:['ai_1']}
  const evidence=await module.exports.verifyAIReplyOrigins(params)
  assert.equal(evidence[0].status,'verified')
  assert.equal(calls[0].headers.Authorization,'Bearer secret-grant')
  assert.equal(JSON.stringify([calls[0].url,calls[0].body]).includes('secret-grant'),false)
  rotateAfterRequest=true
  await assert.rejects(module.exports.verifyAIReplyOrigins(params))
})
