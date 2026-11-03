import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function harness(){
  let user={id:'actor-a',project_id:'project-a',role:'admin'};
  const listeners=[],pending=[];
  const auth={getState:()=>({user}),subscribe:callback=>listeners.push(callback)};
  const create=init=>{let state;const set=patch=>{state={...state,...patch};};state=init(set,()=>state);return Object.assign(()=>state,{getState:()=>state});};
  class APIError extends Error{constructor(status){super('fixture-only error');this.status=status;}}
  const api=new Proxy({}, {get:(_,name)=>(...args)=>new Promise((resolve,reject)=>pending.push({name,args,resolve,reject}))});
  const exports={};
  const code=ts.transpileModule(readFileSync(new URL('../src/stores/businessCustomerIdentityStore.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(code,{exports,require:name=>{
    if(name==='zustand')return{create};
    if(name==='./authStore')return{useAuthStore:auth};
    if(name==='@/services/api')return{APIError};
    if(name==='@/services/businessCustomerIdentityApi')return{BusinessCustomerIdentityApi:api};
    if(name==='@/services/platformsApi')return{platformsApiService:api};
    throw new Error(name);
  }});
  return{store:exports.useBusinessCustomerIdentityStore,pending,APIError,
    reply(name,value){const index=pending.findIndex(item=>item.name===name);assert.ok(index>=0);pending.splice(index,1)[0].resolve(value);},
    deny(name,status){const index=pending.findIndex(item=>item.name===name);pending.splice(index,1)[0].reject(new APIError(status));},
    switch(next){const previous={user};user=next;listeners.forEach(callback=>callback({user},previous));},
  };
}
const policy=(extra={})=>({id:'policy-a',project_id:'project-a',connection_id:'connection-a',scope:'business_customer_identity_configuration',can_enable:false,revision:1,platform_ids:['platform-a'],enabled:true,connection_configuration_revision:1,max_session_seconds:900,signing_key_configured:true,...extra});
function loaded(h,extra={}){h.reply('policy',policy(extra));h.reply('bindings',[]);h.reply('listPlatforms',{data:[{id:'platform-a',project_id:'project-a',name:'网站',is_active:true}]});}
test('foreign or secret-bearing policy is rejected',async()=>{
  for(const extra of [{project_id:'foreign'},{signing_secret:'private-key'},{can_enable:true}]){
    const h=harness();const request=h.store.getState().load('connection-a');loaded(h,extra);await request;
    assert.equal(h.store.getState().error,'invalidResponse');assert.equal(h.store.getState().policy,null);
  }
});
test('late metadata cannot enter a new account',async()=>{
  const h=harness();const request=h.store.getState().load('connection-a');h.switch({id:'actor-b',project_id:'project-b',role:'admin'});loaded(h);await request;
  assert.equal(h.store.getState().policy,null);assert.equal(h.store.getState().connectionId,null);
});
test('unknown save keeps no signing secret and queries current metadata before further changes',async()=>{
  const h=harness();const load=h.store.getState().load('connection-a');loaded(h);await load;
  const save=h.store.getState().save({expected_revision:1,platform_ids:['platform-a'],signing_secret:'fixture-only-signing-key',enabled:true,max_session_seconds:900});
  h.deny('save',503);assert.equal(await save,false);
  assert.equal(h.store.getState().unknown,true);assert.ok(!JSON.stringify(h.store.getState()).includes('fixture-only-signing-key'));
  const reload=h.store.getState().load('connection-a');loaded(h,{revision:2});await reload;assert.equal(h.store.getState().policy.revision,2);
});
test('role change during save clears metadata and ignores the result',async()=>{
  const h=harness();const load=h.store.getState().load('connection-a');loaded(h);await load;
  const save=h.store.getState().save({expected_revision:1,platform_ids:['platform-a'],enabled:false,max_session_seconds:900});
  h.switch({id:'actor-a',project_id:'project-a',role:'staff'});h.reply('save',policy({revision:2,enabled:false}));assert.equal(await save,false);
  assert.equal(h.store.getState().policy,null);
});
