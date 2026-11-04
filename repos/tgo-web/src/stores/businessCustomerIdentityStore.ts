import { create } from 'zustand';
import { BusinessCustomerIdentityApi } from '@/services/businessCustomerIdentityApi';
import { platformsApiService } from '@/services/platformsApi';
import { APIError } from '@/services/api';
import { useAuthStore } from './authStore';
import type { CustomerIdentityBinding, CustomerIdentityPolicy, CustomerIdentityPolicyWrite } from '@/types/businessCustomerIdentity';

interface IdentityState {
  connectionId: string | null; policy: CustomerIdentityPolicy | null; bindings: CustomerIdentityBinding[];
  platforms: Array<{id:string;name:string}>; loading:boolean; saving:boolean; error:string|null; unknown:boolean;
  load:(id:string)=>Promise<void>; save:(body:CustomerIdentityPolicyWrite)=>Promise<boolean>;
  revoke:(binding:CustomerIdentityBinding)=>Promise<boolean>; reset:()=>void;
}
const scope = () => {const user=useAuthStore.getState().user;return user?.role==='admin'?`${user.project_id}:${user.id}`:null;};
let generation=0;
const empty={connectionId:null,policy:null,bindings:[],platforms:[],loading:false,saving:false,error:null,unknown:false};
const errorCode=(error:unknown) => error instanceof APIError ? [401,403].includes(error.status)?'forbidden':error.status===409?'conflict':error.status===422?'invalid':'network':'network';
const validPolicy=(policy:CustomerIdentityPolicy,id:string)=>policy.connection_id===id&&policy.project_id===useAuthStore.getState().user?.project_id&&policy.scope==='business_customer_identity_configuration'&&policy.can_enable===false&&
  Number.isInteger(policy.revision)&&policy.revision>=1&&Number.isInteger(policy.connection_configuration_revision)&&policy.connection_configuration_revision>=1&&Array.isArray(policy.platform_ids)&&typeof policy.enabled==='boolean'&&typeof policy.signing_key_configured==='boolean'&&
  Number.isInteger(policy.max_session_seconds)&&policy.max_session_seconds>=60&&policy.max_session_seconds<=3600&&!('signing_secret' in policy)&&!('signing_ciphertext' in policy);
export const useBusinessCustomerIdentityStore=create<IdentityState>((set,get)=>({
  ...empty,
  reset:()=>{generation++;set(empty);},
  load:async id=>{
    const identity=scope(),current=++generation; set({...empty,connectionId:id,loading:true});
    if(!identity){set({loading:false,error:'forbidden'});return;}
    try {
      const [policy,bindings,platforms]=await Promise.all([
        BusinessCustomerIdentityApi.policy(id).catch(error=>{if(error instanceof APIError&&error.status===404)return null;throw error;}),
        BusinessCustomerIdentityApi.bindings(id),platformsApiService.listPlatforms({limit:100,is_active:true}),
      ]);
      if(current!==generation||identity!==scope())return;
      const user=useAuthStore.getState().user;
      if(policy&&!validPolicy(policy,id))throw new Error('invalidResponse');
      if(bindings.some(row=>row.can_enable!==false||'session_token' in row||'customer_id' in row))throw new Error('invalidResponse');
      if(platforms.data.some(row=>row.project_id!==user?.project_id))throw new Error('invalidResponse');
      set({policy,bindings,platforms:platforms.data.filter(row=>row.is_active&&!row.deleted_at).map(row=>({id:row.id,name:row.name||row.type})),loading:false});
    } catch(error){if(current===generation&&identity===scope())set({...((errorCode(error)==='forbidden')?empty:{}),loading:false,error:error instanceof Error&&error.message==='invalidResponse'?'invalidResponse':errorCode(error)});}
  },
  save:async body=>{
    const {connectionId,saving}=get(),identity=scope(),current=generation;
    if(!connectionId||!identity||saving)return false;
    set({saving:true,error:null});
    try {
      const policy=await BusinessCustomerIdentityApi.save(connectionId,body);
      if(current!==generation||identity!==scope())return false;
      if(!validPolicy(policy,connectionId))throw new Error('invalidResponse');
      set({policy,saving:false,unknown:false}); await get().load(connectionId); return identity===scope()&&get().connectionId===connectionId&&get().error===null;
    } catch(error){if(current===generation&&identity===scope())set({...((errorCode(error)==='forbidden')?empty:{}),saving:false,error:error instanceof Error&&error.message==='invalidResponse'?'invalidResponse':errorCode(error),unknown:errorCode(error)==='network'});return false;}
  },
  revoke:async binding=>{
    const {connectionId,saving}=get(),identity=scope(),current=generation;
    if(!connectionId||!identity||saving)return false;
    set({saving:true,error:null});
    try {const revoked=await BusinessCustomerIdentityApi.revoke(connectionId,binding.id,binding.revision);
      if(current!==generation||identity!==scope())return false;
      if(revoked.id!==binding.id||revoked.status!=='revoked'||revoked.can_enable!==false)throw new Error('invalidResponse');
      set({saving:false});await get().load(connectionId);return true;
    }catch(error){if(current===generation&&identity===scope())set({...((errorCode(error)==='forbidden')?empty:{}),saving:false,error:errorCode(error)});return false;}
  },
}));
useAuthStore.subscribe((state,previous)=>{
  const current=state.user,old=previous.user;
  if(current?.id!==old?.id||current?.project_id!==old?.project_id||current?.role!==old?.role)useBusinessCustomerIdentityStore.getState().reset();
});
