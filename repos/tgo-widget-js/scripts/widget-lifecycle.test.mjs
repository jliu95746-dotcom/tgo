import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import test from 'node:test'
import {setImmediate} from 'node:timers/promises'
import {generationFixture} from './lifecycle-fixture.mjs'

function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
function fixture(){
  const source=fs.readFileSync(new URL('../src/store/chatStore.ts',import.meta.url),'utf8')
  const tree=ts.createSourceFile('store.ts',source,ts.ScriptTarget.Latest,true)
  const selected=[]
  function visit(node){if(ts.isPropertyAssignment(node)&&['endSession','loadInitialHistory','uploadFiles'].includes(node.name.getText(tree)))selected.push(node.getText(tree));ts.forEachChild(node,visit)}
  visit(tree);assert.equal(selected.length,3)
  const epoch=generationFixture(),history=deferred(),upload=deferred(),exports={},sends=[],ends=[]
  let state={apiBase:'http://owned.test',platformApiKey:'old-key',visitorId:'visitor-a',myUid:'a-vtr',channelId:'channel-a',channelType:251,
    messages:[{id:'private-a',payload:{type:1,content:'A-only'},role:'agent'}],staffInfoCache:{a:{name:'A-only'}},
    sessionRevision:0,historyLoading:false,historyHasMore:true,historyError:null,earliestSeq:null}
  const context={...epoch,exports,Date,Math,Map,Set,AbortController,setTimeout,clearTimeout,
    crypto:{randomUUID:()=> 'owned-source'},get:()=>state,set:value=>{state={...state,...(typeof value==='function'?value(state):value)}},
    offMsg:null,offStatus:null,offCustom:null,streamTimer:null,activeParsers:new Map(),uploadControllers:new Map(),pendingFiles:new Map(),inflightStaff:new Set(),
    syncVisitorMessages:()=>history.promise,fetchStaffInfo:()=>{},mapHistoryToChatMessage:message=>({id:message.message_id,role:'agent',payload:message.payload}),
    uploadChatFile:()=>upload.promise,readImageDimensions:async()=>({width:1,height:1}),resolveApiKey:()=> 'current-key',
    endCachedVisitorAccess:async options=>{ends.push(options);return {accessEnded:true,upstreamStopStatus:'pending',imDisconnectStatus:'requested'}},
    IMService:{disconnect:async()=>{},sendPayload:async(...values)=>{sends.push(values);return {reasonCode:1}}},
    requestMediaReply:async()=>sends.push('reply'),ReasonCode:{Success:1,Unknown:0},i18n:{t:value=>value},
  }
  vm.runInNewContext(ts.transpileModule(`exports.actions={${selected.join(',')}}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,context)
  Object.assign(state,exports.actions)
  return {actions:exports.actions,history,upload,sends,ends,state:()=>state,switch:()=>{state={...state,visitorId:'visitor-b',myUid:'b-vtr',channelId:'channel-b',messages:[]}}}
}

test('actual late history result never enters a new visitor or restarts its loading state',async()=>{
  const f=fixture()
  const pending=f.actions.loadInitialHistory()
  await f.actions.endSession();f.switch()
  f.history.resolve({messages:[{message_id:'old-private',message_seq:1,payload:{type:1,content:'A-only'}}],more:0})
  await pending
  assert.equal(JSON.stringify(f.state()).includes('A-only'),false)
  assert.equal(f.state().historyLoading,false)
  assert.equal(f.state().historyError,null)
  assert.equal(f.ends[0].platformApiKey,'old-key')
})

test('actual upload finishing after end cannot send its old file through the new IM connection',async()=>{
  const f=fixture()
  await f.actions.uploadFiles([{type:'image/png',name:'old-private',size:2}])
  await f.actions.endSession();f.switch()
  f.upload.resolve({file_id:'old-file',file_url:'/old-file'})
  await setImmediate()
  assert.equal(f.sends.length,0)
  assert.equal(f.state().messages.length,0)
})

function imFixture(){
  const exports={},connections=[]
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/services/wukongim.ts',import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,{exports,URL,AbortController,setTimeout,clearTimeout,require:()=>({WKIMEvent:{Connect:'connect',Disconnect:'disconnect',Error:'error',Message:'message',CustomEvent:'custom'},
    WKIM:{init:(_url,options)=>{const handlers={};const connection={options,handlers,on:(name,callback)=>{handlers[name]=callback},off:()=>{},disconnect:async()=>{}};connections.push(connection);return connection}}})})
  return {service:new exports.WuKongIMService(),connections}
}
test('actual IM route response from an old init cannot replace the current connection',async()=>{
  const f=imFixture(),old=deferred()
  f.service._fetchRouteWsAddr=()=>old.promise
  const initial=f.service.init({apiBase:'http://a',uid:'a',token:'private-a',target:'a'})
  const rejected=assert.rejects(initial,/会话已变化/)
  await f.service.disconnect()
  f.service._fetchRouteWsAddr=async()=> 'ws://current.test'
  await f.service.init({apiBase:'http://b',uid:'b',token:'private-b',target:'b'})
  old.resolve('ws://old.test');await rejected
  assert.deepEqual(f.connections.map(value=>value.options.uid),['b'])
})
test('queued callbacks from an old native connection cannot reach new message subscribers',async()=>{
  const f=imFixture();f.service._fetchRouteWsAddr=async()=> 'ws://owned.test'
  await f.service.init({apiBase:'http://a',uid:'a',target:'a'})
  const old=f.connections[0].handlers.message
  await f.service.disconnect()
  await f.service.init({apiBase:'http://b',uid:'b',target:'b'})
  const messages=[];f.service.onMessage(value=>messages.push(value))
  old({payload:'A-only'})
  f.connections[1].handlers.message({payload:'B-only'})
  assert.deepEqual(messages.map(value=>value.payload),['B-only'])
})
