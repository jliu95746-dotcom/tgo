import { create } from 'zustand';
import { AgentBusinessConnectionsApi } from '@/services/agentBusinessConnectionsApi';
import { BusinessConnectionsApi } from '@/services/businessConnectionsApi';
import { APIError } from '@/services/api';
import { useAuthStore } from './authStore';
import { useAgentBusinessSummaryStore } from './agentBusinessSummaryStore';
import {useAgentBusinessCapabilityStore} from './agentBusinessCapabilityStore';
import {useAgentBusinessCurrentStateStore} from './agentBusinessCurrentStateStore';
import { validAgentBusinessBinding, validAgentBusinessConfiguration } from '@/utils/agentBusinessConnectionValidation';
import type { AgentBusinessConnection } from '@/types/agentBusinessConnections';
import type { BusinessCapability, BusinessConnection } from '@/types/businessConnections';

interface Pending { requestId: string; connectionId: string }
interface BindingState {
  agentId: string|null; bindings: AgentBusinessConnection[]; connections: BusinessConnection[];
  loading: boolean; saving: boolean; error: string|null; pending: Pending|null;
  hasMore: boolean; loadingMore: boolean; nextOffset: number;
  load: (agentId:string)=>Promise<void>; save:(connectionId:string,operations:BusinessCapability[],enabled:boolean)=>Promise<boolean>;
  loadMore:()=>Promise<void>; recover:()=>Promise<void>; reset:()=>void;
}
const scope=()=>{const user=useAuthStore.getState().user;return user?.role==='admin'?`${user.project_id}:${user.id}`:null;};
const empty={agentId:null,bindings:[],connections:[],loading:false,saving:false,error:null,pending:null,hasMore:false,loadingMore:false,nextOffset:0};
let generation=0;
const storageKey=(identity:string,agent:string)=>`tgo-employee-business-request:${identity}:${agent}`;
const readPending=(identity:string,agent:string):Pending|null=>{
  try {const raw=sessionStorage.getItem(storageKey(identity,agent));if(!raw)return null;const value:unknown=JSON.parse(raw);
    if(typeof value==='object'&&value!==null&&'requestId' in value&&'connectionId' in value&&typeof value.requestId==='string'&&typeof value.connectionId==='string')
      return {requestId:value.requestId,connectionId:value.connectionId};
  } catch { /* A non-writable browser still keeps the current request in memory. */ } return null;
};
const writePending=(identity:string,agent:string,pending:Pending|null)=>{
  try {if(pending)sessionStorage.setItem(storageKey(identity,agent),JSON.stringify(pending));else sessionStorage.removeItem(storageKey(identity,agent));} catch { /* No credentials or command content are persisted. */ }
};
const valid=(record:AgentBusinessConnection,agent:string)=>validAgentBusinessBinding(record,useAuthStore.getState().user?.project_id??'',agent);
const code=(error:unknown)=>error instanceof APIError?[401,403].includes(error.status)?'forbidden':error.status===409?'conflict':error.status===422?'invalid':'network':'invalidResponse';
const validPage=(rows:BusinessConnection[],project:string)=>Array.isArray(rows)&&rows.length<=100&&rows.every(row=>row.project_id===project&&typeof row.id==='string')&&new Set(rows.map(row=>row.id)).size===rows.length;
export const useAgentBusinessConnectionStore=create<BindingState>((set,get)=>({
  ...empty,
  reset:()=>{generation++;set(empty);},
  load:async agent=>{
    const retained=get().agentId===agent?get().pending:null;
    const identity=scope(),current=++generation;set({...empty,agentId:agent,loading:true});
    if(!identity){set({loading:false,error:'forbidden'});return;}
    set({pending:readPending(identity,agent)??retained});
    try {const [result,connections]=await Promise.all([AgentBusinessConnectionsApi.read(agent),BusinessConnectionsApi.list()]);
      if(current!==generation||identity!==scope())return;
      if(!validAgentBusinessConfiguration(result,useAuthStore.getState().user?.project_id??'',agent)||!validPage(connections,result.project_id))throw new Error('invalidResponse');
      const boundIds=[...new Set(result.bindings.map(row=>row.connection_id))].filter(id=>!connections.some(row=>row.id===id));
      const additional=await Promise.all(boundIds.map(async id=>{
        try {const row=await BusinessConnectionsApi.get(id);if(row.id!==id||row.project_id!==result.project_id)throw new Error('invalidResponse');return row;}
        catch(error){if(error instanceof APIError&&error.status===404)return null;throw error;}
      }));
      if(current!==generation||identity!==scope())return;
      set({bindings:result.bindings,connections:[...connections,...additional.filter((row):row is BusinessConnection=>row!==null)],loading:false,hasMore:connections.length===100,nextOffset:connections.length});
    } catch(error){if(current!==generation||identity!==scope())return;set({loading:false,error:code(error)});}
  },
  loadMore:async()=>{
    const {agentId:agent,hasMore,loading,loadingMore,nextOffset}=get(),identity=scope(),current=generation;
    if(!agent||!identity||!hasMore||loading||loadingMore)return;
    set({loadingMore:true,error:null});
    try {const page=await BusinessConnectionsApi.list(nextOffset);
      if(current!==generation||identity!==scope())return;
      const project=useAuthStore.getState().user?.project_id;
      if(!project||!validPage(page,project))throw new Error('invalidResponse');
      const existing=get().connections,newRows=page.filter(row=>!existing.some(value=>value.id===row.id));
      if(page.length===100&&!newRows.length)throw new Error('invalidResponse');
      const values=new Map(existing.map(row=>[row.id,row]));for(const row of page)values.set(row.id,row);
      set({connections:[...values.values()],loadingMore:false,nextOffset:nextOffset+page.length,hasMore:page.length===100});
    }catch(error){if(current!==generation||identity!==scope())return;set({loadingMore:false,error:code(error)});if(code(error)==='forbidden')set({bindings:[],connections:[],hasMore:false});}
  },
  save:async(connection,operations,enabled)=>{
    const {agentId:agent,pending,saving,loading,loadingMore}=get(),identity=scope(),current=generation;
    if(!agent||!identity||pending||saving||loading||loadingMore)return false;
    set({saving:true,error:null});
    const available=await useAgentBusinessCurrentStateStore.getState().check(agent,true);
    if(current!==generation||identity!==scope())return false;
    if(!available){set({saving:false,error:useAgentBusinessCurrentStateStore.getState().error==='active'?'active':'currentState'});return false;}
    const old=get().bindings.find(row=>row.connection_id===connection),requestId=crypto.randomUUID();
    const pointer={requestId,connectionId:connection};set({saving:true,error:null,pending:pointer});writePending(identity,agent,pointer);
    const expected=old?.revision??0;
    try {const record=await AgentBusinessConnectionsApi.save(agent,connection,{request_id:requestId,expected_revision:expected,operations,enabled});
      if(current!==generation||identity!==scope())return false;
      if(!valid(record,agent)||record.connection_id!==connection||record.configured_by!==useAuthStore.getState().user?.id||record.revision!==expected+1||record.enabled!==enabled||[...record.operations].sort().join()!==[...operations].sort().join())throw new Error('invalidResponse');
      writePending(identity,agent,null);set({saving:false,pending:null,bindings:[...get().bindings.filter(row=>row.connection_id!==connection),record]});useAgentBusinessSummaryStore.getState().invalidate(agent);useAgentBusinessCapabilityStore.getState().invalidate(agent);return true;
    } catch(error){if(current!==generation||identity!==scope())return false;
      const rejected=error instanceof APIError&&[400,401,403,404,409,422].includes(error.status);
      if(rejected)writePending(identity,agent,null);
      set({saving:false,error:code(error),pending:rejected?null:pointer});useAgentBusinessSummaryStore.getState().invalidate(agent);useAgentBusinessCapabilityStore.getState().invalidate(agent);
      if(code(error)==='forbidden')set({bindings:[],connections:[]});return false;
    }
  },
  recover:async()=>{
    const {agentId:agent,pending,saving}=get(),identity=scope(),current=generation;
    if(!agent||!pending||!identity||saving)return;set({saving:true,error:null});
    try {const record=await AgentBusinessConnectionsApi.recover(agent,pending.requestId);
      if(current!==generation||identity!==scope())return;
      if(!valid(record,agent)||record.connection_id!==pending.connectionId||record.configured_by!==useAuthStore.getState().user?.id)throw new Error('invalidResponse');
      // Read today's selection: an old successful receipt must not restore a later retired binding.
      writePending(identity,agent,null);set({pending:null});await get().load(agent);useAgentBusinessSummaryStore.getState().invalidate(agent);useAgentBusinessCapabilityStore.getState().invalidate(agent);
    } catch(error){if(current!==generation||identity!==scope())return;set({saving:false,error:code(error)});if(code(error)==='forbidden')set({bindings:[],connections:[]});}
  },
}));
useAuthStore.subscribe((state,previous)=>{const identity=(user:typeof state.user)=>user?`${user.project_id}:${user.id}:${user.role}`:null;
  if(identity(state.user)!==identity(previous.user))useAgentBusinessConnectionStore.getState().reset();});
