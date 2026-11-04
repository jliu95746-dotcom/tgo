import { create } from 'zustand';
import { AgentBusinessConnectionsApi } from '@/services/agentBusinessConnectionsApi';
import { validAgentBusinessConfiguration } from '@/utils/agentBusinessConnectionValidation';
import { useAuthStore } from './authStore';
import type { AgentBusinessConnections } from '@/types/agentBusinessConnections';

interface Entry { data: AgentBusinessConnections|null; loading:boolean; ready:boolean; fetchedAt:number }
interface Store {
  identity:string|null; entries:Record<string,Entry>;
  load:(agent:string,force?:boolean)=>Promise<void>; invalidate:(agent:string)=>void;
}
const identity=()=>{const user=useAuthStore.getState().user;return user?.role==='admin'?`${user.project_id}:${user.id}`:null;};
let generation=0,sequence=0;
const requests=new Map<string,number>();
export const useAgentBusinessSummaryStore=create<Store>((set,get)=>({
  identity:null,entries:{},
  invalidate:agent=>{requests.delete(agent);set(state=>({entries:Object.fromEntries(Object.entries(state.entries).filter(([key])=>key!==agent))}));},
  load:async(agent,force=false)=>{
    const scope=identity(),project=useAuthStore.getState().user?.project_id;
    if(!scope||!project)return;
    const previous=get().identity===scope?get().entries[agent]:undefined;
    if(previous?.loading||(!force&&previous?.ready&&Date.now()-previous.fetchedAt<15000))return;
    if(get().identity!==scope)set({identity:scope,entries:{}});
    const epoch=generation,request=++sequence;requests.set(agent,request);
    const current=()=>epoch===generation&&identity()===scope&&requests.get(agent)===request;
    const put=(entry:Entry)=>set(state=>({entries:{...state.entries,[agent]:entry}}));
    put({data:null,loading:true,ready:false,fetchedAt:0});
    try {const data=await AgentBusinessConnectionsApi.read(agent);
      if(!current())return;
      if(!validAgentBusinessConfiguration(data,project,agent))throw new Error('invalidResponse');
      put({data,loading:false,ready:true,fetchedAt:Date.now()});
    }catch{if(current())put({data:null,loading:false,ready:false,fetchedAt:0});}
    finally{if(requests.get(agent)===request)requests.delete(agent);}
  },
}));
useAuthStore.subscribe((next,previous)=>{
  if(next.user?.id===previous.user?.id&&next.user?.project_id===previous.user?.project_id&&next.user?.role===previous.user?.role)return;
  generation++;requests.clear();useAgentBusinessSummaryStore.setState({identity:null,entries:{}});
});
