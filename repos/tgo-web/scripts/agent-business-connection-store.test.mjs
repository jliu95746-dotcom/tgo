import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import {webcrypto} from 'node:crypto';
import ts from 'typescript';

function harness(writable=true,autoCurrent=true){
  let user={id:'actor',project_id:'project',role:'admin'};const listeners=[],pending=[],storage=new Map();
  const auth={getState:()=>({user}),subscribe:fn=>listeners.push(fn)};
  const create=init=>{let state;const set=patch=>{state={...state,...patch};};state=init(set,()=>state);return Object.assign(()=>state,{getState:()=>state});};
  class APIError extends Error{constructor(status){super('fixture');this.status=status;}}
  const api=new Proxy({}, {get:(_,name)=>(...args)=>new Promise((resolve,reject)=>pending.push({name,args,resolve,reject}))});
  const exports={};const code=ts.transpileModule(readFileSync(new URL('../src/stores/agentBusinessConnectionStore.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(code,{exports,crypto:webcrypto,sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>{if(!writable)throw Error('blocked');storage.set(key,value);},removeItem:key=>storage.delete(key)},require:name=>{
    if(name==='zustand')return{create};if(name==='./authStore')return{useAuthStore:auth};if(name==='@/services/api')return{APIError};
    if(name==='@/services/agentBusinessConnectionsApi')return{AgentBusinessConnectionsApi:api};
    if(name==='@/services/businessConnectionsApi')return{BusinessConnectionsApi:api};
    if(name==='./agentBusinessSummaryStore')return{useAgentBusinessSummaryStore:{getState:()=>({invalidate:()=>{}})}};
    if(name==='./agentBusinessCurrentStateStore')return{useAgentBusinessCurrentStateStore:{getState:()=>({error:'active',check:(agent,paused)=>autoCurrent?Promise.resolve(true):api.checkEmployee(agent,paused)})}};
    if(name==='./agentBusinessCapabilityStore')return{useAgentBusinessCapabilityStore:{getState:()=>({invalidate:()=>{}})}};
    if(name==='@/utils/agentBusinessConnectionValidation'){
      const module={};const compiled=ts.transpileModule(readFileSync(new URL('../src/utils/agentBusinessConnectionValidation.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
      vm.runInNewContext(compiled,{exports:module});return module;
    }throw Error(name);
  }});
  return{store:exports.useAgentBusinessConnectionStore,pending,storage,
    reply(name,value){const i=pending.findIndex(item=>item.name===name);assert.ok(i>=0);pending.splice(i,1)[0].resolve(value);},
    deny(name,status){const i=pending.findIndex(item=>item.name===name);assert.ok(i>=0);pending.splice(i,1)[0].reject(new APIError(status));},
    switch(next){const previous={user};user=next;listeners.forEach(fn=>fn({user},previous));},
  };
}
const record=(extra={})=>({scope:'employee_business_configuration',can_enable:false,id:'binding',project_id:'project',agent_id:'agent',connection_id:'connection',operations:['order_query'],enabled:true,revision:1,configuration_revision:1,authorization_revision:1,resource_revision:'a'.repeat(64),configured_by:'actor',...extra});
function loaded(h,bindings=[]){h.reply('read',{scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:'agent',bindings});h.reply('list',[{id:'connection',project_id:'project',mode:'http',name:'订单'}]);}
async function prepare(h,bindings=[]){const promise=h.store.getState().load('agent');loaded(h,bindings);await promise;}

test('foreign binding or execution claim never enters state',async()=>{
  for(const extra of [{project_id:'foreign'},{can_enable:true},{credentials:{token:'fixture'}}]){
    const h=harness();await prepare(h,[record(extra)]);assert.equal(h.store.getState().bindings.length,0);assert.equal(h.store.getState().error,'invalidResponse');
  }
});
test('late load is fenced after account or role changes',async()=>{
  const h=harness(),load=h.store.getState().load('agent');h.switch({id:'actor',project_id:'project',role:'agent'});loaded(h,[record()]);await load;
  assert.equal(h.store.getState().agentId,null);assert.equal(h.store.getState().bindings.length,0);
});
test('save validates operation and revision rather than trusting a successful HTTP reply',async()=>{
  const h=harness();await prepare(h);const save=h.store.getState().save('connection',['order_query'],true);await new Promise(resolve=>setImmediate(resolve));
  h.reply('save',record({operations:['logistics_query']}));assert.equal(await save,false);
  assert.equal(h.store.getState().bindings.length,0);assert.ok(h.store.getState().pending);
});
test('unknown write keeps only request pointers and blocks another mutation',async()=>{
  const h=harness();await prepare(h);const save=h.store.getState().save('connection',['order_query'],true);await new Promise(resolve=>setImmediate(resolve));
  h.deny('save',503);assert.equal(await save,false);assert.equal(await h.store.getState().save('connection',['order_query'],true),false);await new Promise(resolve=>setImmediate(resolve));
  const saved=JSON.parse([...h.storage.values()][0]);assert.deepEqual(Object.keys(saved).sort(),['connectionId','requestId']);
});
test('unwritable session storage preserves unknown request across a current page refresh',async()=>{
  const h=harness(false);await prepare(h);const save=h.store.getState().save('connection',['order_query'],true);await new Promise(resolve=>setImmediate(resolve));h.deny('save',503);await save;
  const id=h.store.getState().pending.requestId;await prepare(h);assert.equal(h.store.getState().pending.requestId,id);
});
test('recovered original receipt reads current state and never reapplies a later retired binding',async()=>{
  const h=harness();await prepare(h);const save=h.store.getState().save('connection',['order_query'],true);await new Promise(resolve=>setImmediate(resolve));h.deny('save',503);await save;
  const recovery=h.store.getState().recover();h.reply('recover',record());await new Promise(resolve=>setImmediate(resolve));loaded(h,[record({revision:2,enabled:false})]);await recovery;
  assert.equal(h.store.getState().bindings[0].enabled,false);assert.equal(h.store.getState().bindings[0].revision,2);assert.equal(h.store.getState().pending,null);
});

const page=(offset=0)=>Array.from({length:100},(_,index)=>({id:`connection-${offset+index}`,project_id:'project',mode:'http',name:`订单 ${offset+index}`}));
test('a binding outside the first directory page keeps its actual connection name',async()=>{
  const h=harness(),load=h.store.getState().load('agent');
  h.reply('read',{scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:'agent',bindings:[record({connection_id:'connection-150'})]});
  h.reply('list',page());await new Promise(resolve=>setImmediate(resolve));
  if(h.pending.some(row=>row.name==='get'))h.reply('get',page(100)[50]);
  await load;assert.equal(h.store.getState().connections.find(row=>row.id==='connection-150')?.name,'订单 150');
  assert.equal(h.store.getState().hasMore,true);
});
test('directory pagination uses server offsets even when bound resources were added separately',async()=>{
  const h=harness(),load=h.store.getState().load('agent');loaded(h);await load;
  // Reload a full first page, including an independently fetched bound connection.
  const again=h.store.getState().load('agent');h.reply('read',{scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:'agent',bindings:[record({connection_id:'connection-150'})]});h.reply('list',page());
  await new Promise(resolve=>setImmediate(resolve));if(h.pending.some(row=>row.name==='get'))h.reply('get',page(100)[50]);await again;
  assert.equal(typeof h.store.getState().loadMore,'function');
  const more=h.store.getState().loadMore();assert.equal(h.pending.find(row=>row.name==='list').args[0],100);
  h.reply('list',page(100));await more;assert.equal(h.store.getState().connections.length,200);
  assert.equal(new Set(h.store.getState().connections.map(row=>row.id)).size,200);
  const final=h.store.getState().loadMore();h.reply('list',[]);await final;assert.equal(h.store.getState().hasMore,false);
});
test('foreign or late directory pages never update a different actor',async()=>{
  const h=harness(),load=h.store.getState().load('agent');h.reply('read',{scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:'agent',bindings:[]});h.reply('list',page());await load;
  assert.equal(typeof h.store.getState().loadMore,'function');const more=h.store.getState().loadMore();
  h.switch({id:'different',project_id:'project',role:'admin'});h.reply('list',page(100));await more;
  assert.equal(h.store.getState().agentId,null);assert.equal(h.store.getState().connections.length,0);
});
test('a foreign next directory page is rejected while keeping the known first page',async()=>{
  const h=harness(),load=h.store.getState().load('agent');h.reply('read',{scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:'agent',bindings:[]});h.reply('list',page());await load;
  const more=h.store.getState().loadMore();h.reply('list',page(100).map(row=>({...row,project_id:'foreign'})));await more;
  assert.equal(h.store.getState().connections.length,100);assert.equal(h.store.getState().nextOffset,100);assert.equal(h.store.getState().error,'invalidResponse');
});
test('only a definite missing bound connection is shown as unavailable',async()=>{
  for(const status of [404,503]){
    const h=harness(),load=h.store.getState().load('agent');h.reply('read',{scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:'agent',bindings:[record()]});h.reply('list',[]);
    await new Promise(resolve=>setImmediate(resolve));h.deny('get',status);await load;
    assert.equal(h.store.getState().bindings.length,status===404?1:0);
    assert.equal(h.store.getState().error,status===404?null:'network');
  }
});

test('an activated employee cannot save a binding and no unknown request pointer is created',async()=>{
  const h=harness(true,false);await prepare(h);const saving=h.store.getState().save('connection',['order_query'],true);h.reply('checkEmployee',false);assert.equal(await saving,false);
  assert.equal(h.pending.some(row=>row.name==='save'),false);assert.equal(h.store.getState().pending,null);assert.equal(h.storage.size,0);
});
