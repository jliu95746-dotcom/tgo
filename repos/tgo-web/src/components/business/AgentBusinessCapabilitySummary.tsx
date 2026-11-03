import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { useAgentBusinessSummaryStore } from '@/stores/agentBusinessSummaryStore';
import type { BusinessCapability } from '@/types/businessConnections';
import {useAgentBusinessCapabilityStore} from '@/stores/agentBusinessCapabilityStore';

export default function AgentBusinessCapabilitySummary({agentId,onOpen}:{agentId:string;onOpen:()=>void}) {
  const {t}=useTranslation();
  const user=useAuthStore(state=>state.user),state=useAgentBusinessSummaryStore();
  const capabilities=useAgentBusinessCapabilityStore(),[clock,setClock]=useState(Date.now());
  const identity=user?.role==='admin'?`${user.project_id}:${user.id}`:null;
  const entry=state.identity===identity?state.entries[agentId]:undefined;
  const evidence=capabilities.identity===identity?capabilities.entries[agentId]:undefined;
  const load=state.load,loadCapabilities=capabilities.load,exists=Boolean(entry&&evidence);
  useEffect(()=>{if(identity)void Promise.all([load(agentId),loadCapabilities(agentId)]);},[agentId,identity,load,loadCapabilities,exists]);
  useEffect(()=>{const interval=setInterval(()=>setClock(Date.now()),10000);return()=>clearInterval(interval);},[]);
  if(!identity)return null;
  const configured=new Set(entry?.data?.bindings.filter(row=>row.enabled).flatMap(row=>row.operations)??[]);
  const operations:BusinessCapability[]=['order_query','logistics_query','customer_profile_query','membership_query'];
  const status=(operation:BusinessCapability)=>{
    if(!configured.has(operation))return 'notConfigured';
    const bindings=entry?.data?.bindings.filter(row=>row.enabled&&row.operations.includes(operation))??[];
    const rows=evidence?.data?.capabilities.filter(row=>row.operation===operation)??[];
    if(!evidence?.ready||!evidence.data||clock-Date.parse(evidence.data.checked_at)>60000||rows.length!==bindings.length||
      rows.some(row=>!bindings.some(binding=>binding.id===row.binding_id&&binding.connection_id===row.connection_id&&binding.revision===row.binding_revision)))return 'unavailable';
    if(rows.some(row=>row.status==='passed'&&(!row.valid_until||Date.parse(row.valid_until)<=clock)))return 'needs_retest';
    if(rows.every(row=>row.status==='passed'))return 'passed';
    if(rows.some(row=>row.status==='passed'))return 'partial';
    return (['testing','unavailable','needs_retest','test_failed','rejected','review_required','not_tested'] as const).find(value=>rows.some(row=>row.status===value))??'unavailable';
  };
  return <section className="space-y-2 rounded-xl border border-gray-100 bg-gray-50/60 p-3 text-xs dark:border-gray-700 dark:bg-gray-900/20">
    <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium text-gray-700 dark:text-gray-200">{t('businessConnections.employee.summary')}</span>
      <button type="button" disabled={entry?.loading||evidence?.loading} onClick={()=>void Promise.all([load(agentId,true),loadCapabilities(agentId,true)])} className="text-blue-600 disabled:opacity-40">{t('businessConnections.refresh')}</button></div>
    {!entry||entry.loading?<p className="text-gray-500">{t('businessConnections.employee.checking')}</p>:!entry.ready?<p role="status" className="text-amber-700 dark:text-amber-300">{t('businessConnections.employee.unverified')}</p>:
      <ul className="space-y-1">{operations.map(operation=>{const value=status(operation);return <li key={operation} className="flex flex-wrap justify-between gap-2"><span className="text-gray-700 dark:text-gray-200">{t(`businessConnections.${operation}`)}</span><span className={value==='passed'?'text-green-700 dark:text-green-300':value==='notConfigured'?'text-gray-500':'text-amber-700 dark:text-amber-300'}>{t(value==='notConfigured'?'businessConnections.employee.notConfigured':`businessConnections.trial.capability.${value}`)}</span></li>;})}</ul>}
    <button type="button" onClick={onOpen} className="font-medium text-blue-600 hover:underline dark:text-blue-300">{t('businessConnections.employee.configure')}</button>
  </section>;
}
