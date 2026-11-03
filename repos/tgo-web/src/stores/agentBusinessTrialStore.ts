import {create} from 'zustand';
import {APIError} from '@/services/api';
import {AgentBusinessTrialsApi} from '@/services/agentBusinessTrialsApi';
import {sameBusinessReviewCommand,validBusinessReport,validBusinessRequestState,validBusinessReview,validBusinessReviewCommand,validBusinessReviewRequestState,validBusinessReviewState,validCustomerSession} from '@/utils/agentBusinessTrialValidation';
import {useAuthStore} from './authStore';
import {useAgentBusinessCapabilityStore} from './agentBusinessCapabilityStore';
import {useAgentBusinessCurrentStateStore} from './agentBusinessCurrentStateStore';
import type {BusinessCapability} from '@/types/businessConnections';
import type {BusinessRequestState,BusinessReviewChecks,BusinessReviewReason,BusinessReviewRequest,BusinessReviewRequestState,BusinessReviewState,BusinessTrialReport} from '@/types/agentBusinessTrials';

interface Pointer {requestId:string;connectionId:string;operation:BusinessCapability}
interface ReviewPointer {requestId:string;reportId:string;command:BusinessReviewRequest|null}
type ErrorKind='network'|'forbidden'|'missing'|'conflict'|'invalid'|'invalidResponse'|'storage'|'active'|'currentState';
interface State {
  identity:string|null;agentId:string|null;pointer:Pointer|null;report:BusinessTrialReport|null;requestState:BusinessRequestState|null;review:BusinessReviewState|null;
  pendingReview:ReviewPointer|null;loading:boolean;sending:boolean;reviewing:boolean;unknown:boolean;error:ErrorKind|null;
  reviewRequest:BusinessReviewRequestState|null;
  select:(agent:string)=>Promise<void>;reset:()=>void;refresh:()=>Promise<void>;cancel:()=>Promise<void>;
  start:(connection:string,operation:BusinessCapability,order:string,assertion:string)=>Promise<void>;
  submitReview:(decision:'passed'|'rejected',checks:BusinessReviewChecks,reason:BusinessReviewReason|null)=>Promise<void>;
  recoverReview:()=>Promise<void>;
  resumeReview:()=>Promise<void>;cancelReview:()=>Promise<void>;
}
const empty={identity:null,agentId:null,pointer:null,report:null,requestState:null,review:null,pendingReview:null,reviewRequest:null,loading:false,sending:false,reviewing:false,unknown:false,error:null};
let generation=0;let controller:AbortController|null=null;
const scope=()=>{const user=useAuthStore.getState().user;return user?.role==='admin'?`${user.project_id}:${user.id}`:null;};
const storageKey=(identity:string,agent:string)=>`tgo-business-trial:${identity}:${agent}`;
const validOperation=(value:unknown):value is BusinessCapability=>value==='order_query'||value==='logistics_query'||value==='customer_profile_query'||value==='membership_query';
const kind=(error:unknown):ErrorKind=>error instanceof Error&&error.message==='invalidResponse'?'invalidResponse':error instanceof APIError?
  [401,403].includes(error.status)?'forbidden':error.status===404?'missing':error.status===409?'conflict':error.status===422?'invalid':'network':'network';
function save(identity:string,agent:string,pointer:Pointer|null,pendingReview:ReviewPointer|null){
  try{const key=storageKey(identity,agent),value=JSON.stringify({pointer,pendingReview});sessionStorage.setItem(key,value);return sessionStorage.getItem(key)===value;}catch{return false;}
}
function restore(identity:string,agent:string):{pointer:Pointer|null;pendingReview:ReviewPointer|null}{
  try{const value:unknown=JSON.parse(sessionStorage.getItem(storageKey(identity,agent))??'null');
    if(value&&typeof value==='object'&&'pointer' in value){const pointer=value.pointer;
      if(pointer&&typeof pointer==='object'&&'requestId' in pointer&&'connectionId' in pointer&&'operation' in pointer&&typeof pointer.requestId==='string'&&typeof pointer.connectionId==='string'&&validOperation(pointer.operation)){
        const result:{pointer:Pointer;pendingReview:ReviewPointer|null}={pointer:{requestId:pointer.requestId,connectionId:pointer.connectionId,operation:pointer.operation},pendingReview:null};
        if('pendingReview' in value&&value.pendingReview&&typeof value.pendingReview==='object'&&'requestId' in value.pendingReview&&'reportId' in value.pendingReview&&typeof value.pendingReview.requestId==='string'&&typeof value.pendingReview.reportId==='string')result.pendingReview={requestId:value.pendingReview.requestId,reportId:value.pendingReview.reportId,command:'command' in value.pendingReview&&validBusinessReviewCommand(value.pendingReview.command)&&value.pendingReview.command.request_id===value.pendingReview.requestId?value.pendingReview.command:null};
        return result;
      }
    }
  }catch{/* No private command is stored or reconstructed from browser storage. */}
  return{pointer:null,pendingReview:null};
}
export const useAgentBusinessTrialStore=create<State>((set,get)=>{
  const current=(epoch:number,identity:string,agent:string)=>epoch===generation&&identity===scope()&&get().agentId===agent;
  const persist=()=>{const {identity,agentId,pointer,pendingReview}=get();return Boolean(identity&&agentId&&save(identity,agentId,pointer,pendingReview));};
  const invalidate=()=>{const agent=get().agentId;if(agent)useAgentBusinessCapabilityStore.getState().invalidate(agent);};
  const accept=(value:BusinessTrialReport,identity:string,agent:string)=>{
    const user=useAuthStore.getState().user,pointer=get().pointer;
    if(!user||!pointer||!validBusinessReport(value,user.project_id,user.id,agent)||value.request_id!==pointer.requestId||value.connection_id!==pointer.connectionId||value.operation!==pointer.operation)throw Error('invalidResponse');
    if(identity!==scope())return;set({report:value,unknown:false,error:null});
  };
  const acceptRequest=(value:BusinessRequestState,identity:string,agent:string)=>{
    const user=useAuthStore.getState().user,pointer=get().pointer;
    if(!user||!pointer||!validBusinessRequestState(value,user.project_id,user.id,agent)||value.request_id!==pointer.requestId||value.connection_id!==pointer.connectionId||value.operation!==pointer.operation)throw Error('invalidResponse');
    if(identity!==scope())return;
    if(value.report)accept(value.report,identity,agent);
    set({requestState:value,unknown:false,error:null,...(!value.report?{report:null,review:null}:{})});
  };
  const readReview=async(epoch:number,identity:string,agent:string)=>{
    const report=get().report;if(!report||report.status==='running'){set({review:null});return;}
    const value=await AgentBusinessTrialsApi.reviewState(agent,report.id,controller?.signal);
    if(!current(epoch,identity,agent))return;
    const actor=useAuthStore.getState().user?.id;
    if(!actor||!validBusinessReviewState(value,report,actor))throw Error('invalidResponse');set({review:value});
  };
  const acceptReviewResult=(result:Parameters<typeof validBusinessReview>[0],report:BusinessTrialReport,pending:ReviewPointer)=>{
    const actor=useAuthStore.getState().user?.id;
    if(!actor||!validBusinessReview(result,report,actor)||result.request_id!==pending.requestId||pending.reportId!==report.id||pending.command&&
      (result.decision!==pending.command.decision||result.reason!==pending.command.reason||(Object.keys(pending.command.checks) as Array<keyof BusinessReviewChecks>).some(key=>result.checks[key]!==pending.command!.checks[key])))throw Error('invalidResponse');
    set({pendingReview:null,error:null});persist();
  };
  const acceptReviewRequest=(value:BusinessReviewRequestState,report:BusinessTrialReport,pending:ReviewPointer)=>{
    const actor=useAuthStore.getState().user?.id;
    if(!actor||!validBusinessReviewRequestState(value,report,actor)||value.request_id!==pending.requestId||pending.reportId!==report.id||value.command&&pending.command&&!sameBusinessReviewCommand(value.command,pending.command))throw Error('invalidResponse');
    set({reviewRequest:value,error:null});
    if(value.result){acceptReviewResult(value.result,report,pending);return;}
    if(value.status==='cancelled'||value.status==='failed'){set({pendingReview:null});persist();return;}
    if(!pending.command&&value.command){set({pendingReview:{...pending,command:value.command}});persist();}
  };
  return{
    ...empty,
    reset:()=>{generation++;controller?.abort();controller=null;set(empty);},
    select:async agent=>{
      get().reset();const identity=scope();set({identity,agentId:agent});if(!identity){set({error:'forbidden'});return;}
      set(restore(identity,agent));if(get().pointer)await get().refresh();
    },
    refresh:async()=>{
      const {agentId:agent,pointer,loading,sending,reviewing}=get(),identity=scope();if(!agent||!pointer||!identity||loading||sending||reviewing)return;
      const epoch=generation;controller=new AbortController();set({loading:true,review:null,error:null});
      try{
        try{const value=await AgentBusinessTrialsApi.recover(agent,pointer.requestId,controller.signal);if(!current(epoch,identity,agent))return;accept(value,identity,agent);}
        catch(error){if(!current(epoch,identity,agent))return;if(!(error instanceof APIError&&error.status===404))throw error;
          const value=await AgentBusinessTrialsApi.requestState(agent,pointer.requestId,controller?.signal);if(!current(epoch,identity,agent))return;acceptRequest(value,identity,agent);}
        await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent))set({error:kind(error),unknown:!get().report,review:null,...(kind(error)==='forbidden'?{report:null}:{})});}
      finally{if(current(epoch,identity,agent))set({loading:false});}
    },
    start:async(connection,operation,order,assertion)=>{
      const {agentId:agent,pointer,report,requestState,loading,sending,pendingReview,reviewing,unknown}=get(),identity=scope(),user=useAuthStore.getState().user;
      const terminated=requestState?.status==='failed'||requestState?.status==='cancelled';
      if(!agent||!identity||!user||loading||sending||reviewing||pendingReview||unknown||pointer&&(!report&&!terminated||report?.status==='running'))return;
      const needsOrder=operation==='order_query'||operation==='logistics_query';
      if((needsOrder?!/^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$/.test(order):Boolean(order))||!assertion||assertion.length>8192){set({error:'invalid'});return;}
      const epoch=generation;controller=new AbortController();set({sending:true,error:null,review:null});let submitted=false;
      try{
        const available=await useAgentBusinessCurrentStateStore.getState().check(agent,true);if(!current(epoch,identity,agent))return;
        if(!available){set({error:useAgentBusinessCurrentStateStore.getState().error==='active'?'active':'currentState'});return;}
        const session=await AgentBusinessTrialsApi.verifyCustomer(connection,assertion,controller.signal);if(!current(epoch,identity,agent))return;
        if(!validCustomerSession(session,user.project_id,connection))throw Error('invalidResponse');
        const requestId=crypto.randomUUID();set({pointer:{requestId,connectionId:connection,operation},report:null,requestState:null,pendingReview:null,reviewRequest:null});
        if(!persist()){set({pointer:null,error:'storage'});return;}invalidate();submitted=true;
        const value=await AgentBusinessTrialsApi.start(agent,{request_id:requestId,connection_id:connection,operation,visitor_id:session.visitor_id,...(needsOrder?{order_no:order}:{}),customer_session:session.session_token},controller.signal);
        if(!current(epoch,identity,agent))return;accept(value,identity,agent);await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent)){
        set({error:kind(error),unknown:submitted});persist();
      }}finally{if(current(epoch,identity,agent)){set({sending:false});invalidate();}}
    },
    cancel:async()=>{
      const {agentId:agent,report,pointer,loading,sending,reviewing,pendingReview,requestState}=get(),identity=scope();
      if(!agent||!identity||!pointer||loading||sending||reviewing||pendingReview||report&&report.status!=='running'||requestState?.status==='failed'||requestState?.status==='cancelled')return;
      const epoch=generation;controller=new AbortController();set({sending:true,error:null,review:null});invalidate();
      try{if(report){const value=await AgentBusinessTrialsApi.cancel(agent,report.id,controller.signal);if(!current(epoch,identity,agent))return;accept(value,identity,agent);}
        else{const value=await AgentBusinessTrialsApi.cancelRequest(agent,{request_id:pointer.requestId,connection_id:pointer.connectionId,operation:pointer.operation},controller.signal);if(!current(epoch,identity,agent))return;if(value.status==='preparing')throw Error('invalidResponse');acceptRequest(value,identity,agent);}
        await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent))set({error:kind(error),unknown:true,review:null});}
      finally{if(current(epoch,identity,agent)){set({sending:false});invalidate();}}
    },
    submitReview:async(decision,checks,reason)=>{
      const {agentId:agent,report,review,pendingReview,loading,sending,reviewing}=get(),identity=scope();
      if(!agent||!report||!identity||pendingReview||loading||sending||reviewing||report.status==='running'||!review)return;
      if(decision==='passed'&&(!review.can_review||!review.valid_until||Date.parse(review.valid_until)<=Date.now()||!Object.values(checks).every(Boolean)))return;
      const epoch=generation,request:BusinessReviewRequest={request_id:crypto.randomUUID(),expected_latest_review_id:review.latest_review?.id??null,decision,checks:{...checks},reason};
      const pending={requestId:request.request_id,reportId:report.id,command:request};
      controller=new AbortController();set({reviewing:true,pendingReview:pending,error:null,reviewRequest:null});
      if(!persist()){set({reviewing:false,pendingReview:null,error:'storage'});return;}invalidate();
      try{
        const available=await useAgentBusinessCurrentStateStore.getState().check(agent,false);if(!current(epoch,identity,agent))return;
        if(!available){set({pendingReview:null,error:'currentState'});persist();return;}
        set({review:null});
        const result=await AgentBusinessTrialsApi.review(agent,report.id,request,controller.signal);if(!current(epoch,identity,agent))return;
        acceptReviewResult(result,report,pending);await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent)){
        set({error:kind(error)});persist();
      }}finally{if(current(epoch,identity,agent)){set({reviewing:false});invalidate();}}
    },
    recoverReview:async()=>{
      const {agentId:agent,report,pendingReview,loading,sending,reviewing}=get(),identity=scope();if(!agent||!report||!pendingReview||!identity||loading||sending||reviewing)return;
      const epoch=generation;controller=new AbortController();set({reviewing:true,review:null,reviewRequest:null,error:null});
      try{
        try{const result=await AgentBusinessTrialsApi.recoverReview(agent,pendingReview.reportId,pendingReview.requestId,controller.signal);if(!current(epoch,identity,agent))return;acceptReviewResult(result,report,pendingReview);}
        catch(error){if(!current(epoch,identity,agent))return;if(!(error instanceof APIError&&error.status===404))throw error;
          const value=await AgentBusinessTrialsApi.reviewRequestState(agent,pendingReview.reportId,pendingReview.requestId,controller?.signal);if(!current(epoch,identity,agent))return;acceptReviewRequest(value,report,pendingReview);}
        await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent))set({error:kind(error)});}
      finally{if(current(epoch,identity,agent)){set({reviewing:false});invalidate();}}
    },
    resumeReview:async()=>{
      const {agentId:agent,report,pendingReview,reviewRequest,loading,sending,reviewing,error}=get(),identity=scope();
      if(!agent||!report||!pendingReview||!identity||loading||sending||reviewing||!(reviewRequest?.can_resume||error==='missing'&&pendingReview.command))return;
      const epoch=generation;controller=new AbortController();set({reviewing:true,review:null,error:null});invalidate();
      try{
        const available=await useAgentBusinessCurrentStateStore.getState().check(agent,false);if(!current(epoch,identity,agent))return;
        if(!available){set({error:'currentState'});return;}
        const result=reviewRequest?.can_resume?
        await AgentBusinessTrialsApi.resumeReview(agent,pendingReview.reportId,pendingReview.requestId,controller.signal):
        await AgentBusinessTrialsApi.review(agent,pendingReview.reportId,pendingReview.command!,controller.signal);
        if(!current(epoch,identity,agent))return;acceptReviewResult(result,report,pendingReview);set({reviewRequest:null});await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent))set({error:kind(error),reviewRequest:null});}
      finally{if(current(epoch,identity,agent)){set({reviewing:false});invalidate();}}
    },
    cancelReview:async()=>{
      const {agentId:agent,report,pendingReview,loading,sending,reviewing}=get(),identity=scope();if(!agent||!report||!pendingReview||!identity||loading||sending||reviewing)return;
      const epoch=generation;controller=new AbortController();set({reviewing:true,review:null,error:null});invalidate();
      try{const value=await AgentBusinessTrialsApi.cancelReviewRequest(agent,pendingReview.reportId,pendingReview.requestId,controller.signal);if(!current(epoch,identity,agent))return;
        if(value.status==='pending')throw Error('invalidResponse');acceptReviewRequest(value,report,pendingReview);await readReview(epoch,identity,agent);
      }catch(error){if(current(epoch,identity,agent))set({error:kind(error),reviewRequest:null});}
      finally{if(current(epoch,identity,agent)){set({reviewing:false});invalidate();}}
    },
  };
});
useAuthStore.subscribe((state,previous)=>{if(state.user?.id!==previous.user?.id||state.user?.project_id!==previous.user?.project_id||state.user?.role!==previous.user?.role)useAgentBusinessTrialStore.getState().reset();});
