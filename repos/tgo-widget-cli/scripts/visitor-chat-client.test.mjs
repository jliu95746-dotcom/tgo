import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from '../../tgo-widget-js/node_modules/typescript/lib/typescript.js'

const token = 'owned-cli-header-grant'
const body = {api_key:'public-key',message:'你好',from_uid:'visitor',stream:true}
function fixture({fault,events=[]}={}) {
  let current = true
  const calls=[], outputs=[], clients={}, business={}, sessions={}
  const proof={imToken:token,assertCurrent:() => {if (!current) throw new Error('聊天访问已变化')}}
  const compile = (file,exports,extra) => vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,{exports,...extra})
  compile('client.ts',clients,{require:() => ({}),Response,TextDecoder,AbortController,setTimeout,clearTimeout,
    fetch:async (...args) => {
      calls.push(args)
      if (fault==='fetch') current=false
      if (body.stream && args[1].body && JSON.parse(args[1].body).stream) {
        let index=0
        return {ok:true,body:{getReader:() => ({read:async () => {
          if (fault==='read') current=false
          if (index===events.length) return {done:true}
          const event=events[index++]
          return {done:false,value:new TextEncoder().encode('data: '+JSON.stringify(event)+'\n\n')}
        },cancel:async () => {}})}}
      }
      return {ok:true,status:200,statusText:'OK',headers:new Headers(),text:async () => {
        if (fault==='body') current=false
        return JSON.stringify({success:true,content:fault==='echo' ? token : '已发货'})
      }}
    },
  })
  compile('businessCustomerSession.ts',sessions,{require:() => ({})})
  compile('businessChat.ts',business,{require:name => name==='./client.js' ? clients : sessions,
    process:{stdout:{write:value => outputs.push(value)},stderr:{write:value => outputs.push(value)}}})
  const client = new clients.TgoWidgetClient({server:'http://owned.test',apiKey:'public-key'})
  return {client,proof,calls,outputs,send:() => business.sendBusinessChat(client,body,[],proof.assertCurrent,proof)}
}

test('original visitor JSON grant stays in header, not body or URL',async () => {
  const f=fixture()
  const result=await f.client.postVisitor('/v1/chat/completion',{...body,stream:false},f.proof)
  assert.equal(result.content,'已发货')
  assert.equal(f.calls[0][1].headers.Authorization,'Bearer '+token)
  assert.equal(JSON.stringify([f.calls[0][0],f.calls[0][1].body]).includes(token),false)
})
for (const fault of ['fetch','body','echo']) test('JSON refuses '+fault+' before returning a result',async () => {
  const f=fixture({fault})
  await assert.rejects(f.client.postVisitor('/v1/chat/completion',{...body,stream:false},f.proof))
})
for (const events of [
  [{event_type:'agent_content_chunk',data:{content_chunk:'unconfirmed'}}],
  [{event_type:'workflow_failed',data:{error_message:'failed'}}],
  [{event_type:'agent_content_chunk',data:{content_chunk:'owned-cli-'}},
    {event_type:'agent_content_chunk',data:{content_chunk:'header-grant'}},
    {event_type:'workflow_completed',data:{success:true,final_content:'safe-final'}}],
  [{event_type:'workflow_completed',data:{success:true,final_content:token}}],
]) test('unfinished, failed or echoed SSE yields no CLI content '+events.length,async () => {
  const f=fixture({events})
  await assert.rejects(f.send())
  assert.deepEqual(f.outputs,[])
})
test('a changed visitor during the actual stream read releases no output',async () => {
  const f=fixture({fault:'read',events:[{event_type:'workflow_completed',data:{success:true,final_content:'private'}}]})
  await assert.rejects(f.send())
  assert.deepEqual(f.outputs,[])
})
test('successful SSE prints only the verified final reply',async () => {
  const f=fixture({events:[
    {event_type:'agent_content_chunk',data:{content_chunk:'draft'}},
    {event_type:'agent_response_complete',data:{success:true,final_content:'已发货'}},
    {event_type:'workflow_completed',data:{}},
  ]})
  await f.send()
  assert.deepEqual(f.outputs,['已发货\n'])
  assert.equal(f.calls[0][1].headers.Authorization,'Bearer '+token)
  assert.equal(JSON.stringify([f.calls[0][0],f.calls[0][1].body]).includes(token),false)
})
for (const event_type of ['ai_disabled','assist_mode','queued','human_handoff']) test(event_type+' stays an acknowledgement',async () => {
  const f=fixture({events:[{event_type}]})
  const result=await f.send()
  assert.equal(result.event_type,event_type)
  assert.deepEqual(f.outputs,[JSON.stringify({event_type})+'\n'])
})
