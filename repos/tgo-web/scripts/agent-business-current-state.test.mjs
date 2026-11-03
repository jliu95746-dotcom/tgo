import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';

function harness(){
  let user={id:'actor',project_id:'project',role:'admin'};const pending=[],calls=[],listeners=[];
  const create=init=>{let state;const set=patch=>{state={...state,...(typeof patch==='function'?patch(state):patch)};};state=init(set,()=>state);return Object.assign(()=>state,{getState:()=>state});};
  class APIError extends Error{constructor(status){super('fixture');this.status=status;}}
  const api={currentEmployee:(...args)=>{calls.push(args);return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
  const source=ts.transpileModule(readFileSync(new URL('../src/stores/agentBusinessCurrentStateStore.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,exports={};
  vm.runInNewContext(source,{exports,Date,AbortController,require:name=>{
    if(name==='zustand')return{create};if(name==='@/services/api')return{APIError};if(name==='@/services/agentBusinessTrialsApi')return{AgentBusinessTrialsApi:api};
    if(name==='./authStore')return{useAuthStore:{getState:()=>({user}),subscribe:fn=>listeners.push(fn)}};throw Error(name);
  }});
  return{store:exports.useAgentBusinessCurrentStateStore,calls,resolve:value=>pending.shift().resolve(value),reject:status=>pending.shift().reject(new APIError(status)),change(next){const before={user};user=next;listeners.forEach(fn=>fn({user},before));}};
}
const snapshot=(extra={})=>({scope:'employee_business_current_state',can_enable:false,project_id:'project',actor_id:'actor',agent_id:'agent',active:false,checked_at:new Date().toISOString(),...extra});

test('every mutation checks a new server snapshot; a previous paused snapshot cannot pass activation in another window',async()=>{
  const h=harness(),first=h.store.getState().check('agent',true);h.resolve(snapshot());assert.equal(await first,true);
  const second=h.store.getState().check('agent',true);h.resolve(snapshot({active:true}));assert.equal(await second,false);assert.equal(h.calls.length,2);assert.equal(h.store.getState().error,'active');
});
test('review and recovery can read a serving employee without authorizing new test queries',async()=>{
  const h=harness(),check=h.store.getState().check('agent',false);h.resolve(snapshot({active:true}));assert.equal(await check,true);assert.equal(h.store.getState().snapshot.active,true);
});
test('a foreign, secret-bearing or stale snapshot never permits mutation',async()=>{
  for(const change of [{project_id:'foreign'},{actor_id:'foreign'},{agent_id:'foreign'},{can_enable:true},{order_no:'PRIVATE'},{active:undefined},{checked_at:new Date(Date.now()-120000).toISOString()}]){
    const h=harness(),check=h.store.getState().check('agent',true);h.resolve(snapshot(change));assert.equal(await check,false);assert.equal(h.store.getState().snapshot,null);
  }
});
test('a changed login drops the late snapshot',async()=>{
  const h=harness(),check=h.store.getState().check('agent',true);h.change({id:'other',project_id:'project',role:'admin'});h.resolve(snapshot());assert.equal(await check,false);assert.equal(h.store.getState().snapshot,null);
});
test('a network failure removes the previous paused snapshot',async()=>{
  const h=harness(),first=h.store.getState().check('agent',true);h.resolve(snapshot());await first;
  const second=h.store.getState().check('agent',true);h.reject(503);assert.equal(await second,false);assert.equal(h.store.getState().snapshot,null);
});
