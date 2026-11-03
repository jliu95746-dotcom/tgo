const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const vm=require('node:vm')
const test=require('node:test')
const id='11111111-1111-4111-8111-111111111111'
const options={apiBase:'https://owned.test/api',platformApiKey:'public-key',apiKey:'public-key',channelId:'visitor-vtr',channelType:251,filePath:'owned-fixture'}
const oldUrl='https://untrusted.test/v1/chat/files/'+id+'?file_token=old'
function fixture(mode) {
  let cache={visitor_id:'visitor',channel_id:'visitor-vtr',im_token:'private-original-grant'}
  const calls=[]
  const link=options.apiBase+'/v1/chat/files/'+id+'?file_token=owned-file-only'
  function load(file) {
    const module={exports:{}}
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/services',file),'utf8'),{
      module,Date,
      require:key=>key==='./visitor'?{loadCachedVisitor:()=>cache}:key==='./visitorDataAccess'?load('visitorDataAccess.js'):{
        request:async init=>{calls.push(init);return {ok:true,json:async()=>{
          if(mode==='stale')cache={...cache,visitor_id:'replacement'}
          return {access_url:mode==='echo'?cache.im_token:mode==='evil'?'https://untrusted.test/file':link,
            expires_at:new Date(Date.now()+60000).toISOString()}
        }}}},
      wx:{uploadFile:init=>{
        calls.push(init)
        if(mode==='stale')cache={...cache,visitor_id:'replacement'}
        init.success({statusCode:200,data:JSON.stringify({file_id:id,file_url:mode==='echo'?cache.im_token:link,
          channel_id:options.channelId,channel_type:251})})
        return {abort(){},onProgressUpdate(){}}
      }}
    })
    return module.exports
  }
  return {file:load('chatFileAccess.js'),upload:load('upload.js'),calls,clear:()=>{cache=null}}
}
test('mini file links use only the configured API and header-only visitor authority',async()=>{
  const f=fixture(),link=await f.file.authorizeChatFileUrl(oldUrl,options)
  assert.equal(link,options.apiBase+'/v1/chat/files/'+id+'?file_token=owned-file-only')
  assert.equal(f.calls[0].headers.Authorization,'Bearer private-original-grant')
  assert.equal(f.calls[0].url.includes('untrusted.test'),false)
  assert.equal(f.calls[0].url.includes('private-original-grant'),false)
  const text=await f.file.authorizeChatMarkdown('![图]('+oldUrl+')',options)
  assert.equal(text.includes('private-original-grant'),false)
  assert.ok(text.includes('owned-file-only'))
})
test('mini structured UI masks nested private images and Markdown before authorizing them',async()=>{
  const f=fixture()
  const parts=[{type:'data-spec',data:{type:'patch',patch:{op:'add',path:'/elements/photo',
    value:{type:'Image',props:{url:oldUrl,caption:'图片'}}}}},
  {type:'text',text:'资料：![图]('+oldUrl+')'},
  {type:'data-spec',data:{type:'patch',patch:{op:'add',path:'/elements/public',
    value:{type:'Image',props:{src:'https://public.test/photo.png'}}}}}]
  const masked=f.file.maskPrivateChatStructured(parts)
  assert.equal(masked.changed,true)
  assert.equal(JSON.stringify(masked.value).includes(oldUrl),false)
  assert.equal(masked.value[0].data.patch.value.props.url,'')
  assert.equal(masked.value[2].data.patch.value.props.src,'https://public.test/photo.png')
  const authorized=await f.file.authorizeChatStructured(parts,options)
  assert.equal(JSON.stringify(authorized).includes(oldUrl),false)
  assert.equal(authorized[0].data.patch.value.props.url.includes('owned-file-only'),true)
  assert.equal(authorized[1].text.includes('owned-file-only'),true)
  assert.equal(authorized[2].data.patch.value.props.src,'https://public.test/photo.png')
  assert.equal(f.calls.every(call=>!call.url.includes('untrusted.test')),true)
})
test('mini structured UI never restores unsafe private file links',async()=>{
  const f=fixture('evil')
  const parts=[{type:'data-spec',data:{value:{props:{src:oldUrl}}}}]
  const authorized=await f.file.authorizeChatStructured(parts,options)
  assert.equal(authorized[0].data.value.props.src,'')
})
for(const mode of ['stale','echo','evil'])test('mini file rejects '+mode+' responses',async()=>{
  const f=fixture(mode)
  await assert.rejects(f.file.authorizeChatFileUrl(oldUrl,options),error=>!error.message.includes('private-original-grant'))
})
test('mini upload never publishes missing, stale or credential-bearing responses',async()=>{
  const f=fixture();await f.upload.uploadChatFile(options)
  assert.equal(f.calls[0].header.Authorization,'Bearer private-original-grant')
  assert.equal(JSON.stringify(f.calls[0].formData).includes('private-original-grant'),false)
  const empty=fixture();empty.clear();await assert.rejects(empty.upload.uploadChatFile(options));assert.equal(empty.calls.length,0)
  const stale=fixture('stale');await assert.rejects(stale.upload.uploadChatFile(options))
  const echo=fixture('echo');await assert.rejects(echo.upload.uploadChatFile(options),error=>!error.message.includes('private-original-grant'))
})

test('mini image component hides historical links and ignores a previous session response',async()=>{
  let definition
  const pending=[]
  const file={isPrivateChatFile:()=>true,maskPrivateChatMarkdown:value=>value || '',
    readChatFileAccess:()=>new Promise(resolve=>pending.push(resolve)),authorizeChatMarkdown:async value=>value,
    maskPrivateChatStructured:value=>({value,changed:false}),authorizeChatStructured:async value=>value}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/components/message-list/index.js'),'utf8'),{
    Component:value=>{definition=value},setTimeout,clearTimeout,
    require:name=>name.includes('chatFileAccess')?file:name.includes('types')?{isSystemMessageType:()=>false}:{formatMessageTime:()=>''}
  })
  const props={messages:[{id:'A',payload:{type:2,url:oldUrl}}],apiBase:options.apiBase,platformApiKey:'public-key',sessionRevision:1}
  const component={properties:props,data:{...definition.data},setData(value){Object.assign(this.data,value)}}
  Object.assign(component,definition.methods)
  definition.lifetimes.attached.call(component)
  assert.equal(component.data.displayMessages[0].payload.url,'')
  props.messages=[{id:'B',payload:{type:2,url:oldUrl}}];props.sessionRevision=2
  component._processMessages(props.messages)
  pending[0]({url:'old-private-A',expiresAt:Date.now()+60000})
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(component.data.displayMessages[0].id,'B')
  assert.equal(component.data.displayMessages[0].payload.url,'')
  pending[1]({url:'current-B-file-only',expiresAt:Date.now()+60000})
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(component.data.displayMessages[0].payload.url,'current-B-file-only')
  definition.lifetimes.detached.call(component)
})

test('mini message list hides JSON Render private files until own visitor links resolve',async()=>{
  let definition
  const f=fixture()
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/components/message-list/index.js'),'utf8'),{
    Component:value=>{definition=value},setTimeout,clearTimeout,
    require:name=>name.includes('chatFileAccess')?f.file:name.includes('types')?{
      isSystemMessageType:()=>false,MSG_TYPE_VOICE:4}:{formatMessageTime:()=>''}
  })
  const parts=[{type:'data-spec',data:{type:'patch',patch:{op:'add',path:'/elements/photo',
    value:{type:'Image',props:{url:oldUrl}}}}},{type:'text',text:'![图]('+oldUrl+')'}]
  const props={messages:[{id:'A',role:'agent',payload:{type:1,content:''},uiParts:parts}],
    apiBase:options.apiBase,platformApiKey:options.platformApiKey,sessionRevision:1}
  const component={properties:props,data:{...definition.data},setData(value){Object.assign(this.data,value)}}
  Object.assign(component,definition.methods)
  definition.lifetimes.attached.call(component)
  assert.equal(JSON.stringify(component.data.displayMessages).includes(oldUrl),false)
  await new Promise(resolve=>setImmediate(resolve))
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(JSON.stringify(component.data.displayMessages).includes('owned-file-only'),true)
  assert.equal(JSON.stringify(component.data.displayMessages).includes(oldUrl),false)
  definition.lifetimes.detached.call(component)
})
