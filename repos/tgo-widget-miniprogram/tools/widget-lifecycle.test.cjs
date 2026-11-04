const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const business = require('../src/services/businessCustomerSession')

function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return {promise,resolve} }
function load(file, dependencies, extras = {}) {
  const module = {exports:{}}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src',file),'utf8'), {
    module, require: name => {
      if (name in dependencies) return dependencies[name]
      throw new Error('Missing fixture dependency: '+name)
    }, setTimeout, clearTimeout, console:{error(){},warn(){}}, ...extras
  })
  return module.exports
}
function fixture(history = Promise.resolve({messages:[],more:0}), upload = Promise.resolve({file_url:'old-file'}), overrides = {}) {
  const events = {}, sent = [], ended = []
  const im = {isReady:()=>true,disconnect:()=>Promise.resolve(),sendPayload:async payload=>{sent.push(payload);return {reasonCode:1}}}
  for (const event of ['Message','Status','Custom']) im['on'+event] = cb => {events[event]=cb;return ()=>{}}
  const store = load('core/chatStore.js', {
    '../services/wukongim':im, '../services/visitor':{loadCachedVisitor:()=>null},
    '../services/visitorLifecycle':{endCachedVisitorAccess:options=>{ended.push(options);return Promise.resolve({accessEnded:true})},retryPendingVisitorEnds:()=>Promise.resolve([])},
    '../services/businessCustomerSession':business, '../services/messageHistory':{syncVisitorMessages:()=>history},
    '../services/chat':{}, '../services/upload':{getImageInfo:async()=>({width:1,height:1}),uploadChatFile:()=>upload},
    '../adapters/systemInfo':{collectSystemInfo:()=>null}, './types':{mapHistoryToChatMessage:m=>m}, './platformStore':{},
    '../utils/uid':{generateClientMsgNo:()=> 'old-source'}, '../utils/jsonRender':{}, ...overrides
  })
  Object.assign(store._state,{apiBase:'http://original.test',platformApiKey:'original-key',myUid:'old-vtr',
    visitorId:'old',projectId:'project',platformId:'platform',channelId:'old-channel',channelType:251,messages:[{id:'old-message'}]})
  return {store,im,events,sent,ended}
}

test('end immediately clears identity and messages and ignores the old history result', async () => {
  const wait=deferred(), f=fixture(wait.promise)
  const history=f.store.loadInitialHistory()
  const ending=f.store.endSession()
  assert.equal(f.store.getState().visitorId,'')
  assert.equal(f.store.getState().messages.length,0)
  Object.assign(f.store._state,{visitorId:'new',messages:[{id:'new-message'}],historyLoading:true})
  wait.resolve({messages:[{id:'private-old-result',time:new Date()}],more:0})
  await Promise.all([history,ending])
  assert.equal(f.store.getState().messages[0].id,'new-message')
  assert.equal(f.store.getState().historyLoading,true)
  assert.equal(f.ended[0].platformApiKey,'original-key')
})

test('an old image upload cannot send its result to the new visitor', async () => {
  const wait=deferred(), f=fixture(undefined,wait.promise)
  const uploading=f.store.uploadImage('old-image')
  await new Promise(resolve=>setImmediate(resolve))
  await f.store.endSession()
  Object.assign(f.store._state,{visitorId:'new',myUid:'new-vtr',channelId:'new-channel',messages:[]})
  wait.resolve({file_url:'private-old-file'})
  await uploading
  assert.equal(f.sent.length,0)
  assert.equal(f.store.getState().messages.length,0)
})

test('a queued old IM callback cannot deliver data after end', async () => {
  const f=fixture(); f.store._bindIMEvents()
  const oldStatus=f.events.Status
  await f.store.endSession()
  oldStatus('connected')
  assert.equal(f.store.getState().online,false)
})

test('disposing a former component owner cannot end the replacement owner', async () => {
  const f=fixture(), first=f.store.claimSessionOwner(), second=f.store.claimSessionOwner()
  const count=f.ended.length
  await first.release()
  assert.equal(f.ended.length,count)
  assert.equal(second.isCurrent(),true)
  await second.release()
  assert.equal(second.isCurrent(),false)
})

test('an old IM route response cannot replace the new connection', async () => {
  const route=deferred(), inits=[]
  const im=load('services/wukongim.js', {'easyjssdk':{WKIM:{init:(addr,config)=>{
    inits.push(config.uid);return {on(){},off(){},disconnect:()=>Promise.resolve()}
  }},WKIMEvent:{}},'../adapters/request':{request:options=>options.url.includes('old-vtr')?route.promise:
    Promise.resolve({ok:true,json:async()=>({ws_addr:'ws://new.test'})})}})
  const old=im.init({apiBase:'http://owned.test',uid:'old-vtr'})
  await im.disconnect()
  await im.init({apiBase:'http://owned.test',uid:'new-vtr'})
  route.resolve({ok:true,json:async()=>({ws_addr:'ws://old.test'})})
  await assert.rejects(old)
  assert.deepEqual(inits,['new-vtr'])
  assert.equal(im.getUid(),'new-vtr')
})

test('provider state is captured before registration and stale releases do not invalidate its successor', () => {
  const provider=business.createProvider(), release=provider.set(async()=>[])
  const old=provider.capture()
  provider.set(async()=>[])
  assert.throws(old.assertCurrent)
  const current=provider.capture()
  release()
  assert.doesNotThrow(current.assertCurrent)
  assert.equal(current.hasProvider,true)
})

test('a pending registration cannot initialize IM or restore state after end', async () => {
  const wait=deferred(), inits=[]
  const f=fixture(undefined,undefined,{'../services/visitor':{refreshVisitorSession:()=>wait.promise}})
  f.im.isReady=()=>false;f.im.init=async config=>{inits.push(config)}
  const running=f.store.initIM({apiBase:'http://original.test',platformApiKey:'original-key'})
  await f.store.endSession()
  wait.resolve({visitor_id:'late',channel_id:'late-channel',im_token:'late-im'})
  await running
  assert.equal(inits.length,0)
  assert.equal(f.store.getState().visitorId,'')
  assert.equal(f.store.getState().initializing,false)
})

test('customer invalidation clears the old data synchronously before restarting', async () => {
  const wait=deferred(), f=fixture(undefined,undefined,{'../services/visitor':{refreshVisitorSession:()=>wait.promise}})
  f.im.isReady=()=>false
  f.store._sessionActive=true
  const pending=f.store.notifyBusinessCustomerChanged()
  assert.equal(f.store.getState().messages.length,0)
  assert.equal(f.store.getState().myUid,'')
  await f.store.endSession()
  wait.resolve({visitor_id:'stale',channel_id:'stale-channel',im_token:'stale-im'})
  await pending
  assert.equal(f.store.getState().visitorId,'')
})

test('changing the API authority ends the original once and registers the replacement', async () => {
  let ready=true,ends=0
  const registrations=[]
  const f=fixture(undefined,undefined,{
    '../services/visitor':{refreshVisitorSession:async options=>{
      registrations.push(options.platformApiKey);return {visitor_id:'new',channel_id:'new-channel',im_token:'new-im'}
    }},
    '../services/visitorLifecycle':{endCachedVisitorAccess:async()=>{
      ends++;if(ends>3)throw new Error('Recursive initialization refused in fixture');return {accessEnded:true}
    },retryPendingVisitorEnds:async()=>[]}
  })
  f.im.isReady=()=>ready;f.im.disconnect=async()=>{ready=false}
  f.im.init=async()=>{ready=true};f.im.connect=async()=>{}
  f.store._sessionActive=true
  await f.store.initIM({apiBase:'http://replacement.test',platformApiKey:'replacement-key'})
  assert.deepEqual(registrations,['replacement-key'])
  assert.equal(ends,1)
  assert.equal(f.store.getState().apiBase,'http://replacement.test')
  await f.store.endSession()
})

test('ending aborts an active native upload task and ignores its later rejection', async () => {
  let reject, aborted=0
  const f=fixture(undefined,undefined,{'../services/upload':{
    getImageInfo:async()=>null,uploadChatFile:params=>{
      params.onTask({abort(){aborted++;reject(new Error('aborted old upload'))}})
      return new Promise((resolve,fail)=>{reject=fail})
    }
  }})
  const uploading=f.store.uploadImage('old-image')
  await new Promise(resolve=>setImmediate(resolve))
  await f.store.endSession()
  await uploading
  assert.equal(aborted,1)
  assert.equal(f.sent.length,0)
  assert.equal(f.store.getState().messages.length,0)
})

test('a disconnected native IM callback cannot reach listeners of its replacement', async () => {
  const connections=[]
  const im=load('services/wukongim.js', {'easyjssdk':{WKIM:{init:()=>{
    const handlers={};const connection={handlers,on:(name,callback)=>{handlers[name]=callback},off(){},disconnect:()=>Promise.resolve()}
    connections.push(connection);return connection
  }},WKIMEvent:{Message:'message'}},'../adapters/request':{request:async()=>({ok:true,json:async()=>({ws_addr:'ws://owned.test'})})}})
  await im.init({apiBase:'http://owned.test',uid:'old-vtr'})
  const callback=connections[0].handlers.message
  await im.disconnect()
  await im.init({apiBase:'http://owned.test',uid:'new-vtr'})
  const messages=[];im.onMessage(value=>messages.push(value))
  callback({content:'private-old-data'})
  connections[1].handlers.message({content:'new-data'})
  assert.equal(messages.length,1)
  assert.equal(messages[0].content,'new-data')
})

function component(file, dependencies, extras={}) {
  let definition
  load(file,dependencies,{...extras,Component:value=>{definition=value}})
  return definition
}
test('former component detach and hide cannot end the active replacement', async () => {
  const f=fixture();await f.store.endSession()
  const def=component('chat/index.js',{'../core/chatStore':f.store,
    '../core/platformStore':{subscribe:()=>()=>{},init(){},markWelcomeInjected(){}},'../core/i18n':{setLang(){},t:key=>key}})
  function mount(){
    const instance={properties:{apiBase:'http://original.test',apiKey:'original-key'},data:{},...def.methods,
      setData:partial=>Object.assign(instance.data,partial)}
    def.lifetimes.attached.call(instance);return instance
  }
  const first=mount(),second=mount()
  await new Promise(resolve=>setImmediate(resolve))
  f.store._setState({visitorId:'current',messages:[{id:'current-message'}]})
  const count=f.ended.length
  def.lifetimes.detached.call(first);def.pageLifetimes.hide.call(first)
  assert.equal(f.ended.length,count)
  assert.equal(f.store.getState().visitorId,'current')
  assert.equal(second.data.messages[0].id,'current-message')
  def.pageLifetimes.hide.call(second)
  assert.equal(f.store.getState().messages.length,0)
  def.lifetimes.detached.call(second)
})

test('input reset drops a late image picker result from the former visitor', () => {
  let choose, events=[]
  const def=component('components/message-input/index.js',{'../../core/i18n':{t:key=>key}}, {wx:{chooseMedia:options=>{choose=options.success}}})
  const input={properties:{sessionRevision:0,disabled:false},data:{inputValue:'private old draft'},...def.methods,
    setData:partial=>Object.assign(input.data,partial),triggerEvent:(...args)=>events.push(args)}
  def.lifetimes.attached.call(input)
  input.onChooseImage()
  input.properties.sessionRevision=1
  def.observers.sessionRevision.call(input)
  choose({tempFiles:[{tempFilePath:'private-old-image'}]})
  assert.equal(input.data.inputValue,'')
  assert.equal(events.length,0)
  input.onChooseImage();choose({tempFiles:[{tempFilePath:'new-image'}]})
  assert.equal(events.length,1)
  def.lifetimes.detached.call(input)
  choose({tempFiles:[{tempFilePath:'detached-image'}]})
  assert.equal(events.length,1)
})

test('late platform configuration cannot restore the former enterprise branding or welcome', async () => {
  const old=deferred()
  const store=load('core/platformStore.js',{'../services/platform':{fetchPlatformInfo:params=>
    params.platformApiKey==='old-key'?old.promise:Promise.resolve({config:{widget_title:'new title'}})},
    '../adapters/storage':{getJSON:()=>null,setJSON(){}}})
  const first=store.init('http://old.test','old-key')
  await store.init('http://new.test','new-key')
  old.resolve({config:{widget_title:'old title',welcome_message:'old welcome'}})
  await first
  assert.equal(store.getState().config.widget_title,'new title')
  assert.equal(store.getState().config.welcome_message,undefined)
})
