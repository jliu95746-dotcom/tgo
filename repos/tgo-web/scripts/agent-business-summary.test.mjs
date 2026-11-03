import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';

function harness(){
  let user={id:'actor',project_id:'project',role:'admin'};const listeners=[],pending=[];
  const create=init=>{let state;const set=patch=>{state={...state,...(typeof patch==='function'?patch(state):patch)};};state=init(set,()=>state);return Object.assign(()=>state,{getState:()=>state,setState:set});};
  const auth={getState:()=>({user}),subscribe:fn=>listeners.push(fn)};
  const api={read:agent=>new Promise((resolve,reject)=>pending.push({agent,resolve,reject}))};
  function module(path){const exports={};const code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
    vm.runInNewContext(code,{exports,Date,Map,require:name=>{
      if(name==='zustand')return{create};if(name==='./authStore')return{useAuthStore:auth};
      if(name==='@/services/agentBusinessConnectionsApi')return{AgentBusinessConnectionsApi:api};
      if(name==='@/utils/agentBusinessConnectionValidation')return module('../src/utils/agentBusinessConnectionValidation.ts');
      throw Error(name);
    }});return exports;}
  return {store:module('../src/stores/agentBusinessSummaryStore.ts').useAgentBusinessSummaryStore,pending,
    switch(next){const previous={user};user=next;listeners.forEach(fn=>fn({user},previous));}};
}
const binding=(agent='agent',extra={})=>({scope:'employee_business_configuration',can_enable:false,id:`binding-${agent}`,project_id:'project',agent_id:agent,connection_id:'connection',operations:['order_query'],enabled:true,revision:1,configuration_revision:1,authorization_revision:1,resource_revision:'a'.repeat(64),configured_by:'actor',...extra});
const receipt=(agent='agent',extra={})=>({scope:'employee_business_configuration',can_enable:false,project_id:'project',agent_id:agent,bindings:[binding(agent)],...extra});
test('cards keep each employee configuration separate and never treat it as execution proof',async()=>{
  const h=harness(),first=h.store.getState().load('first'),second=h.store.getState().load('second');
  h.pending.find(row=>row.agent==='second').resolve(receipt('second'));h.pending.find(row=>row.agent==='first').resolve(receipt('first'));await Promise.all([first,second]);
  assert.equal(h.store.getState().entries.first.data.agent_id,'first');assert.equal(h.store.getState().entries.second.data.can_enable,false);
});
test('unknown or secret-bearing summaries and late responses after role changes are unavailable',async()=>{
  for(const patch of [{can_enable:true},{bindings:[binding('agent',{token:'fixture'})]},{bindings:[binding('another')]},{bindings:[binding(),binding()]}]){
    const h=harness(),load=h.store.getState().load('agent');h.pending[0].resolve(receipt('agent',patch));await load;
    assert.equal(h.store.getState().entries.agent.ready,false);assert.equal(h.store.getState().entries.agent.data,null);
  }
  const h=harness(),load=h.store.getState().load('agent');h.switch({id:'actor',project_id:'project',role:'agent'});h.pending[0].resolve(receipt());await load;
  assert.deepEqual(Object.keys(h.store.getState().entries),[]);
});
test('a save invalidation fences an in-flight old card response',async()=>{
  const h=harness(),old=h.store.getState().load('agent');h.store.getState().invalidate('agent');
  const current=h.store.getState().load('agent');h.pending[1].resolve(receipt('agent',{bindings:[binding('agent',{enabled:false,revision:2})]}));await current;
  h.pending[0].resolve(receipt());await old;assert.equal(h.store.getState().entries.agent.data.bindings[0].enabled,false);
});
