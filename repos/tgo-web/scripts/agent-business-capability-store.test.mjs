import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';

function harness(){
  let user={id:'actor',project_id:'project',role:'admin'};const pending=[],listeners=[];
  const create=init=>{let state;const set=patch=>{state={...state,...(typeof patch==='function'?patch(state):patch)};};state=init(set,()=>state);return Object.assign(()=>state,{getState:()=>state,setState:set});};
  const api={capabilities:agent=>new Promise((resolve,reject)=>pending.push({agent,resolve,reject}))};
  function module(path){const exports={},code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
    vm.runInNewContext(code,{exports,Date,Set,Map,require:name=>{
      if(name==='zustand')return{create};if(name==='./authStore')return{useAuthStore:{getState:()=>({user}),subscribe:fn=>listeners.push(fn)}};
      if(name==='@/services/agentBusinessTrialsApi')return{AgentBusinessTrialsApi:api};
      if(name==='@/utils/agentBusinessTrialValidation')return module('../src/utils/agentBusinessTrialValidation.ts');throw Error(name);
    }});return exports;
  }
  return {store:module('../src/stores/agentBusinessCapabilityStore.ts').useAgentBusinessCapabilityStore,pending,
    switch(next){const before={user};user=next;listeners.forEach(fn=>fn({user},before));}};
}
const result=(agent='agent')=>({scope:'employee_business_capabilities',can_enable:false,can_execute:false,project_id:'project',agent_id:agent,checked_by:'actor',checked_at:new Date().toISOString(),capabilities:[{binding_id:'binding',binding_revision:1,connection_id:'connection',operation:'order_query',status:'passed',report_id:'report',review_id:'review',valid_until:new Date(Date.now()+60000).toISOString(),issues:[]}]});
test('each employee has a separate current business review summary',async()=>{
  const h=harness(),one=h.store.getState().load('one'),two=h.store.getState().load('two');
  h.pending[1].resolve(result('two'));h.pending[0].resolve(result('one'));await Promise.all([one,two]);
  assert.equal(h.store.getState().entries.one.data.agent_id,'one');assert.equal(h.store.getState().entries.two.data.can_execute,false);
});
test('a role change and a mutation invalidate late green responses',async()=>{
  const h=harness(),read=h.store.getState().load('agent');h.switch({id:'actor',project_id:'project',role:'agent'});h.pending[0].resolve(result());await read;
  assert.deepEqual(Object.keys(h.store.getState().entries),[]);
  const g=harness(),old=g.store.getState().load('agent');g.store.getState().invalidate('agent');const fresh=g.store.getState().load('agent');
  g.pending[1].resolve({...result(),capabilities:[]});await fresh;g.pending[0].resolve(result());await old;
  assert.equal(g.store.getState().entries.agent.data.capabilities.length,0);
});
test('expired, foreign, secret-bearing and execution-granting summaries are refused',async()=>{
  for(const change of [{project_id:'foreign'},{can_enable:true},{can_execute:true},{credentials:'private'},
    {checked_at:new Date(Date.now()-120000).toISOString()},{capabilities:[{...result().capabilities[0],valid_until:new Date(Date.now()-1).toISOString()}]}]){
    const h=harness(),read=h.store.getState().load('agent');h.pending[0].resolve({...result(),...change});await read;
    assert.equal(h.store.getState().entries.agent.ready,false);assert.equal(h.store.getState().entries.agent.data,null);
  }
});
