import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';

function harness(storage=new Map(),storageWritable=true,autoCurrent=true){
  let user={id:'actor',project_id:'project',role:'admin'};const pending=[],calls=[],listeners=[];
  const create=init=>{let state;const set=patch=>{state={...state,...(typeof patch==='function'?patch(state):patch)};};state=init(set,()=>state);return Object.assign(()=>state,{getState:()=>state,setState:set});};
  class APIError extends Error{constructor(status){super('fixture');this.status=status;}}
  const api=new Proxy({}, {get:(_,name)=>(...args)=>{calls.push({name,args});return new Promise((resolve,reject)=>pending.push({name,resolve,reject}));}});
  function module(path){const exports={};const code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
    vm.runInNewContext(code,{exports,Date,Set,Map,JSON,AbortController,crypto:{randomUUID},sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>{if(!storageWritable)throw Error('storage disabled');storage.set(key,value);},removeItem:key=>storage.delete(key)},require:name=>{
      if(name==='zustand')return {create};if(name==='./authStore')return{useAuthStore:{getState:()=>({user}),subscribe:fn=>listeners.push(fn)}};
      if(name==='@/services/api')return{APIError};if(name==='@/services/agentBusinessTrialsApi')return{AgentBusinessTrialsApi:api};
      if(name==='@/utils/agentBusinessTrialValidation')return module('../src/utils/agentBusinessTrialValidation.ts');
      if(name==='./agentBusinessCurrentStateStore')return{useAgentBusinessCurrentStateStore:{getState:()=>({error:'active',check:(agent,paused)=>autoCurrent?Promise.resolve(true):api.checkEmployee(agent,paused)})}};
      if(name==='./agentBusinessCapabilityStore')return{useAgentBusinessCapabilityStore:{getState:()=>({invalidate:()=>{}})}};
      throw Error(name);
    }});return exports;}
  const take=name=>{const index=pending.findIndex(row=>row.name===name);assert.notEqual(index,-1,name);return pending.splice(index,1)[0];};
  return {store:module('../src/stores/agentBusinessTrialStore.ts').useAgentBusinessTrialStore,calls,storage,
    resolve:(name,value)=>take(name).resolve(value),reject:(name,status=0)=>take(name).reject(new APIError(status)),
    change(next){const before={user};user=next;listeners.forEach(fn=>fn({user},before));}};
}
const date=()=>new Date().toISOString();
const future=()=>new Date(Date.now()+3600000).toISOString();
const session=()=>({scope:'business_customer_identity',can_enable:false,binding_id:'customer-binding',project_id:'project',connection_id:'connection',visitor_id:'visitor',platform_id:'platform',revision:1,expires_at:future(),session_token:'private-customer-session'});
const report=(request,extra={})=>({scope:'employee_business_trial',can_enable:false,assessment:'not_reviewed',id:'report',project_id:'project',actor_id:'actor',agent_id:'agent',connection_id:'connection',request_id:request,operation:'order_query',status:'running',configuration_fingerprint:'b'.repeat(64),created_at:date(),expires_at:future(),completed_at:null,error_code:null,receipt:null,...extra});
function complete(value){return {...value,status:'completed',completed_at:date(),receipt:{scope:'employee_business_trial',can_enable:false,mode:'http',project_id:'project',actor_id:'actor',agent_id:'agent',connection_id:'connection',report_id:'report',request_id:value.request_id,attempt_id:'attempt',operation:'order_query',binding_id:'binding',binding_revision:1,configuration_fingerprint:'b'.repeat(64),expected_revision:1,configuration_revision:1,authorization_revision:1,resource_revision:'a'.repeat(64),configuration_test_id:'config-test',configuration_test_valid_until:future(),outcome:'passed',summary:{status:'已发货'},error_code:null,customer_evidence:{binding_id:'customer-binding',binding_revision:1,policy_revision:1,expires_at:future()},checked_at:date()}};}
const reviewState=extra=>({scope:'employee_business_operation_review',can_enable:false,project_id:'project',agent_id:'agent',report_id:'report',checked_by:'actor',checked_at:date(),status:'current',review_status:'not_reviewed',issues:[],can_review:true,valid_until:future(),latest_review:null,reviews:[],...extra});
const checks={ownership_checked:true,result_checked:true,readonly_scope_checked:true};
const reviewRecord=body=>({scope:'employee_business_operation_review',can_enable:false,id:'review',project_id:'project',agent_id:'agent',report_id:'report',connection_id:'connection',operation:'order_query',reviewed_by:'actor',request_id:body.request_id,revision:1,criteria_version:1,report_digest:'c'.repeat(64),configuration_fingerprint:'b'.repeat(64),decision:body.decision,checks:body.checks,reason:body.reason,created_at:date(),valid_until:future()});
async function start(h){await h.store.getState().select('agent');const promise=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');await new Promise(resolve=>setImmediate(resolve));h.resolve('verifyCustomer',session());await new Promise(resolve=>setImmediate(resolve));const command=h.calls.find(row=>row.name==='start').args[1];h.resolve('start',report(command.request_id));await promise;return report(command.request_id);}

test('private inputs remain in the request only; persisted recovery contains identifiers',async()=>{
  const h=harness();await start(h);
  const stored=[JSON.stringify(h.store.getState()),...h.storage.values()].join('');
  for(const secret of ['PRIVATE-ORDER-123','private-merchant-assertion','private-customer-session'])assert.equal(stored.includes(secret),false);
  assert.equal(h.calls.filter(row=>row.name==='start').length,1);assert.ok(h.store.getState().pointer.requestId);
});
test('a lost submission blocks another query and recovers the original after reload',async()=>{
  const h=harness();await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');await new Promise(resolve=>setImmediate(resolve));
  h.resolve('verifyCustomer',session());await new Promise(resolve=>setImmediate(resolve));h.reject('start');await send;
  const command=h.calls.find(row=>row.name==='start').args[1];assert.equal(h.store.getState().unknown,true);
  await h.store.getState().start('connection','order_query','OTHER-ORDER','different');await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls.filter(row=>row.name==='start').length,1);
  const reopened=harness(h.storage),load=reopened.store.getState().select('agent');reopened.resolve('recover',report(command.request_id));await load;
  assert.equal(reopened.store.getState().report.request_id,command.request_id);assert.equal(reopened.calls.filter(row=>row.name==='start').length,0);
});
test('a login change during customer verification cannot submit under the new user',async()=>{
  const h=harness();await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');await new Promise(resolve=>setImmediate(resolve));
  h.change({id:'different',project_id:'project',role:'admin'});h.resolve('verifyCustomer',session());await send;
  assert.equal(h.calls.filter(row=>row.name==='start').length,0);assert.equal(h.store.getState().report,null);
});
test('foreign or secret-bearing customer receipts cannot start a business test',async()=>{
  for(const change of [{can_enable:true},{project_id:'foreign'},{customer_id:'private-extra'}]){
    const h=harness();await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');await new Promise(resolve=>setImmediate(resolve));
    h.resolve('verifyCustomer',{...session(),...change});await send;assert.equal(h.calls.filter(row=>row.name==='start').length,0);
  }
});
test('current review is separate; unknown review recovers its immutable original receipt',async()=>{
  const h=harness(),running=await start(h),refresh=h.store.getState().refresh();h.resolve('recover',complete(running));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState());await refresh;
  const submit=h.store.getState().submitReview('passed',checks,null);await new Promise(resolve=>setImmediate(resolve));const command=h.calls.find(row=>row.name==='review').args[2];h.reject('review');await submit;
  assert.ok(h.store.getState().pendingReview);await h.store.getState().submitReview('rejected',checks,'wrong_result');await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls.filter(row=>row.name==='review').length,1);
  const recover=h.store.getState().recoverReview();h.resolve('recoverReview',reviewRecord(command));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState({review_status:'passed',latest_review:reviewRecord(command),reviews:[reviewRecord(command)]}));await recover;
  assert.equal(h.store.getState().pendingReview,null);assert.equal(h.store.getState().review.review_status,'passed');
});
test('incomplete checks never submit an approval',async()=>{
  const h=harness(),running=await start(h),refresh=h.store.getState().refresh();h.resolve('recover',complete(running));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState());await refresh;
  await h.store.getState().submitReview('passed',{...checks,result_checked:false},null);await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls.filter(row=>row.name==='review').length,0);
});

const requestState=(request,extra={})=>({scope:'employee_business_trial_request',can_enable:false,project_id:'project',actor_id:'actor',agent_id:'agent',request_id:request,connection_id:'connection',operation:'order_query',status:'preparing',created_at:date(),deadline_at:future(),completed_at:null,error_code:null,report:null,...extra});
async function unknown(h,status=0){await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');await new Promise(resolve=>setImmediate(resolve));h.resolve('verifyCustomer',session());await new Promise(resolve=>setImmediate(resolve));const command=h.calls.find(row=>row.name==='start').args[1];h.reject('start',status);await send;return command;}
test('a report 404 reads durable preparation; a terminal receipt allows the next test',async()=>{
  const h=harness(),command=await unknown(h),refresh=h.store.getState().refresh();h.reject('recover',404);await new Promise(resolve=>setImmediate(resolve));
  h.resolve('requestState',requestState(command.request_id,{status:'failed',completed_at:date(),error_code:'preparation_timeout'}));await refresh;
  assert.equal(h.store.getState().unknown,false);assert.equal(h.store.getState().requestState.status,'failed');
  const next=h.store.getState().start('connection','order_query','NEXT-ORDER-123','next-assertion');await new Promise(resolve=>setImmediate(resolve));h.reject('verifyCustomer',422);await next;
  assert.equal(h.calls.filter(row=>row.name==='verifyCustomer').length,2);
});
test('cancel an unknown request by its original key without any private input',async()=>{
  const h=harness(),command=await unknown(h),cancel=h.store.getState().cancel();const body=h.calls.find(row=>row.name==='cancelRequest').args[1];
  assert.deepEqual(JSON.parse(JSON.stringify(body)),{request_id:command.request_id,connection_id:'connection',operation:'order_query'});
  h.resolve('cancelRequest',requestState(command.request_id,{status:'cancelled',completed_at:date(),error_code:'cancelled'}));await cancel;
  assert.equal(h.store.getState().unknown,false);assert.equal(h.store.getState().requestState.status,'cancelled');
});
test('missing request remains fenced until an explicit cancellation receipt',async()=>{
  const h=harness();await unknown(h);const refresh=h.store.getState().refresh();h.reject('recover',404);await new Promise(resolve=>setImmediate(resolve));h.reject('requestState',404);await refresh;
  await h.store.getState().start('connection','order_query','OTHER-ORDER','different');await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls.filter(row=>row.name==='start').length,1);assert.equal(h.store.getState().unknown,true);
});
test('a foreign cancellation or an unresolved preparation cannot clear the fence',async()=>{
  for(const change of [{project_id:'foreign'}, {}, {status:'cancelled',completed_at:date(),error_code:'cancelled',order_no:'PRIVATE'}]){
    const h=harness(),command=await unknown(h),cancel=h.store.getState().cancel();h.resolve('cancelRequest',requestState(command.request_id,change));await cancel;
    assert.equal(h.store.getState().unknown,true);assert.equal(h.store.getState().error,'invalidResponse');
  }
});
test('a rejected submission keeps the original key until its durable state is known',async()=>{
  const h=harness();await unknown(h,409);assert.equal(h.store.getState().unknown,true);assert.ok(h.store.getState().pointer);
});
test('unwritable recovery storage blocks dispatch and never retains private inputs',async()=>{
  const h=harness(new Map(),false);await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');await new Promise(resolve=>setImmediate(resolve));h.resolve('verifyCustomer',session());await send;
  assert.equal(h.calls.filter(row=>row.name==='start').length,0);assert.equal(h.store.getState().error,'storage');assert.equal(h.store.getState().pointer,null);
});

const savedReview=(body,extra={})=>({scope:'employee_business_review_request',can_enable:false,project_id:'project',actor_id:'actor',agent_id:'agent',report_id:'report',request_id:body.request_id,status:'pending',created_at:date(),deadline_at:future(),completed_at:null,checked_at:date(),can_resume:true,processing:false,error_code:null,command:body,result:null,...extra});
async function unknownReview(h){const running=await start(h),refresh=h.store.getState().refresh();h.resolve('recover',complete(running));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState());await refresh;const submit=h.store.getState().submitReview('passed',checks,null);await new Promise(resolve=>setImmediate(resolve));const body=h.calls.find(row=>row.name==='review').args[2];h.reject('review');await submit;return {running,body};}
test('reopen restores only the closed original review command and its original version',async()=>{
  const h=harness(),{running,body}=await unknownReview(h),reopened=harness(h.storage),select=reopened.store.getState().select('agent');reopened.resolve('recover',complete(running));await new Promise(resolve=>setImmediate(resolve));reopened.resolve('reviewState',reviewState());await select;
  assert.deepEqual(JSON.parse(JSON.stringify(reopened.store.getState().pendingReview.command)),JSON.parse(JSON.stringify(body)));
  const recover=reopened.store.getState().recoverReview();reopened.reject('recoverReview',404);await new Promise(resolve=>setImmediate(resolve));reopened.resolve('reviewRequestState',savedReview(body));await new Promise(resolve=>setImmediate(resolve));reopened.resolve('reviewState',reviewState());await recover;
  const resume=reopened.store.getState().resumeReview();await new Promise(resolve=>setImmediate(resolve));assert.equal(reopened.calls.find(row=>row.name==='resumeReview').args[2],body.request_id);reopened.resolve('resumeReview',reviewRecord(body));await new Promise(resolve=>setImmediate(resolve));reopened.resolve('reviewState',reviewState());await resume;assert.equal(reopened.store.getState().pendingReview,null);
  const stored=[...h.storage.values()].join('');for(const secret of ['PRIVATE-ORDER-123','private-customer-session','private-merchant-assertion'])assert.equal(stored.includes(secret),false);
});
test('a review absent from both endpoints retries the exact original command only after explicit continue',async()=>{
  const h=harness(),{body}=await unknownReview(h),recover=h.store.getState().recoverReview();h.reject('recoverReview',404);await new Promise(resolve=>setImmediate(resolve));h.reject('reviewRequestState',404);await recover;
  assert.equal(h.calls.filter(row=>row.name==='review').length,1);const retry=h.store.getState().resumeReview();await new Promise(resolve=>setImmediate(resolve));const commands=h.calls.filter(row=>row.name==='review');assert.deepEqual(commands[1].args[2],commands[0].args[2]);assert.equal(commands[1].args[2].expected_latest_review_id,null);
  h.resolve('review',reviewRecord(body));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState());await retry;assert.equal(h.store.getState().pendingReview,null);
});
test('a changed saved command is rejected without allowing another judgment',async()=>{
  const h=harness(),{body}=await unknownReview(h),recover=h.store.getState().recoverReview();h.reject('recoverReview',404);await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewRequestState',savedReview({...body,expected_latest_review_id:'different-version'}));await recover;
  assert.equal(h.store.getState().error,'invalidResponse');assert.ok(h.store.getState().pendingReview);await h.store.getState().resumeReview();await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls.filter(row=>row.name==='resumeReview').length,0);
});
test('original review cancellation recovers without modifying a later current review',async()=>{
  const h=harness(),{body}=await unknownReview(h),cancel=h.store.getState().cancelReview();const call=h.calls.find(row=>row.name==='cancelReviewRequest');assert.equal(call.args[2],body.request_id);
  h.resolve('cancelReviewRequest',savedReview(body,{status:'cancelled',completed_at:date(),can_resume:false,error_code:'cancelled'}));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState());await cancel;assert.equal(h.store.getState().pendingReview,null);assert.equal(h.store.getState().reviewRequest.status,'cancelled');
});
test('cancel acknowledgement must be terminal and cannot carry another customer or report',async()=>{
  for(const change of [{}, {status:'cancelled',completed_at:date(),can_resume:false,error_code:'cancelled',report_id:'foreign'}]){
    const h=harness(),{body}=await unknownReview(h),cancel=h.store.getState().cancelReview();h.resolve('cancelReviewRequest',savedReview(body,change));await cancel;assert.ok(h.store.getState().pendingReview);assert.equal(h.store.getState().error,'invalidResponse');
  }
});
test('an identity change drops late resume callbacks',async()=>{
  const h=harness(),{body}=await unknownReview(h),recover=h.store.getState().recoverReview();h.reject('recoverReview',404);await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewRequestState',savedReview(body));await new Promise(resolve=>setImmediate(resolve));h.resolve('reviewState',reviewState());await recover;
  const resume=h.store.getState().resumeReview();await new Promise(resolve=>setImmediate(resolve));h.change({id:'different',project_id:'project',role:'admin'});h.resolve('resumeReview',reviewRecord(body));await resume;assert.equal(h.store.getState().review,null);assert.equal(h.store.getState().pendingReview,null);
});

test('employee activation in another window blocks new query before customer exchange',async()=>{
  const h=harness(new Map(),true,false);await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');h.resolve('checkEmployee',false);await send;
  assert.equal(h.calls.filter(row=>row.name==='verifyCustomer').length,0);assert.equal(h.calls.filter(row=>row.name==='start').length,0);assert.equal(h.store.getState().pointer,null);
});
test('identity switch during employee check cannot send a customer exchange',async()=>{
  const h=harness(new Map(),true,false);await h.store.getState().select('agent');const send=h.store.getState().start('connection','order_query','PRIVATE-ORDER-123','private-merchant-assertion');h.change({id:'different',project_id:'project',role:'admin'});h.resolve('checkEmployee',true);await send;
  assert.equal(h.calls.filter(row=>row.name==='verifyCustomer').length,0);assert.equal(h.store.getState().report,null);
});
