import {create} from 'zustand';
import {AgentBusinessTrialsApi} from '@/services/agentBusinessTrialsApi';
import {validBusinessCapabilities} from '@/utils/agentBusinessTrialValidation';
import {useAuthStore} from './authStore';
import type {AgentBusinessCapabilities} from '@/types/agentBusinessTrials';

interface Entry {data:AgentBusinessCapabilities|null;loading:boolean;ready:boolean;fetchedAt:number}
interface Store {identity:string|null;entries:Record<string,Entry>;load:(agent:string,force?:boolean)=>Promise<void>;invalidate:(agent:string)=>void}
const scope=()=>{const user=useAuthStore.getState().user;return user?.role==='admin'?`${user.project_id}:${user.id}`:null;};
let generation=0,sequence=0;const requests=new Map<string,number>();
export const useAgentBusinessCapabilityStore=create<Store>((set,get)=>({
  identity:null,entries:{},
  invalidate:agent=>{requests.delete(agent);set(state=>({entries:Object.fromEntries(Object.entries(state.entries).filter(([key])=>key!==agent))}));},
  load:async(agent,force=false)=>{
    const identity=scope(),user=useAuthStore.getState().user;if(!identity||!user)return;
    const old=get().identity===identity?get().entries[agent]:undefined;
    if(old?.loading||(!force&&old?.ready&&Date.now()-old.fetchedAt<15000))return;
    if(get().identity!==identity)set({identity,entries:{}});
    const epoch=generation,version=++sequence;requests.set(agent,version);
    const current=()=>epoch===generation&&identity===scope()&&requests.get(agent)===version;
    const put=(entry:Entry)=>set(state=>({entries:{...state.entries,[agent]:entry}}));
    put({data:null,loading:true,ready:false,fetchedAt:0});
    try{const data=await AgentBusinessTrialsApi.capabilities(agent);if(!current())return;
      if(!validBusinessCapabilities(data,user.project_id,user.id,agent))throw Error('invalidResponse');
      put({data,loading:false,ready:true,fetchedAt:Date.now()});
    }catch{if(current())put({data:null,loading:false,ready:false,fetchedAt:0});}
    finally{if(requests.get(agent)===version)requests.delete(agent);}
  },
}));
useAuthStore.subscribe((state,previous)=>{if(state.user?.id===previous.user?.id&&state.user?.project_id===previous.user?.project_id&&state.user?.role===previous.user?.role)return;
  generation++;requests.clear();useAgentBusinessCapabilityStore.setState({identity:null,entries:{}});});
