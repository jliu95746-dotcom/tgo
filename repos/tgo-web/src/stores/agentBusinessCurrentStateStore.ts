import {create} from 'zustand';
import {APIError} from '@/services/api';
import {AgentBusinessTrialsApi} from '@/services/agentBusinessTrialsApi';
import {useAuthStore} from './authStore';
import type {BusinessEmployeeCurrentState} from '@/types/agentBusinessTrials';

type ErrorKind='active'|'forbidden'|'network'|'invalidResponse';
interface State {
  identity:string|null;agentId:string|null;snapshot:BusinessEmployeeCurrentState|null;loading:boolean;error:ErrorKind|null;
  check:(agent:string,requirePaused:boolean)=>Promise<boolean>;refresh:()=>Promise<void>;reset:()=>void;
}
const empty={identity:null,agentId:null,snapshot:null,loading:false,error:null};
const scope=()=>{const user=useAuthStore.getState().user;return user?.role==='admin'?`${user.project_id}:${user.id}`:null;};
let generation=0;let controller:AbortController|null=null;
export const useAgentBusinessCurrentStateStore=create<State>((set,get)=>({
  ...empty,
  reset:()=>{generation++;controller?.abort();controller=null;set(empty);},
  refresh:async()=>{const agent=get().agentId;if(agent)await get().check(agent,false);},
  check:async(agent,requirePaused)=>{
    const identity=scope(),user=useAuthStore.getState().user,current=++generation;
    controller?.abort();controller=new AbortController();set({...empty,identity,agentId:agent,loading:true});
    if(!identity||!user){set({loading:false,error:'forbidden'});return false;}
    try{
      const value=await AgentBusinessTrialsApi.currentEmployee(agent,controller.signal);
      if(current!==generation||identity!==scope())return false;
      const age=value&&typeof value.checked_at==='string'?Date.now()-Date.parse(value.checked_at):Number.NaN;
      if(!value||value.scope!=='employee_business_current_state'||value.can_enable!==false||value.project_id!==user.project_id||value.actor_id!==user.id||value.agent_id!==agent||typeof value.active!=='boolean'||!Number.isFinite(age)||age< -30000||age>60000||
        Object.keys(value).some(key=>!['scope','can_enable','project_id','actor_id','agent_id','active','checked_at'].includes(key)))throw Error('invalidResponse');
      set({snapshot:value,loading:false,error:requirePaused&&value.active?'active':null});
      return !requirePaused||!value.active;
    }catch(error){if(current!==generation||identity!==scope())return false;
      set({snapshot:null,loading:false,error:error instanceof APIError&&[401,403].includes(error.status)?'forbidden':error instanceof Error&&error.message==='invalidResponse'?'invalidResponse':'network'});return false;
    }
  },
}));
useAuthStore.subscribe((state,previous)=>{if(state.user?.id!==previous.user?.id||state.user?.project_id!==previous.user?.project_id||state.user?.role!==previous.user?.role)useAgentBusinessCurrentStateStore.getState().reset();});
