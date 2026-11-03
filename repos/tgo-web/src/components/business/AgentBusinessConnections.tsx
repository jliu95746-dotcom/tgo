import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useAgentBusinessConnectionStore } from '@/stores/agentBusinessConnectionStore';
import type { BusinessCapability } from '@/types/businessConnections';
import AgentBusinessTrialPanel from './AgentBusinessTrialPanel';
import AgentBusinessCurrentState from './AgentBusinessCurrentState';
import {useAgentBusinessCurrentStateStore} from '@/stores/agentBusinessCurrentStateStore';

export default function AgentBusinessConnections({agentId}:{agentId:string;active?:boolean}) {
  const {t}=useTranslation();const state=useAgentBusinessConnectionStore();
  const {load,reset}=state;
  const current=useAgentBusinessCurrentStateStore();
  const [selected,setSelected]=useState('');const [operations,setOperations]=useState<BusinessCapability[]>([]);
  const text=(key:string)=>t(`businessConnections.employee.${key}`);
  useEffect(()=>{void load(agentId);return()=>reset();},[agentId,load,reset]);
  const {check,reset:resetCurrent}=current;
  useEffect(()=>{void check(agentId,false);
    const refresh=()=>{if(document.visibilityState==='visible'&&!useAgentBusinessCurrentStateStore.getState().loading)void check(agentId,false);};
    window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
    return()=>{window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);resetCurrent();};
  },[agentId,check,resetCurrent]);
  const snapshot=current.agentId===agentId?current.snapshot:null;
  const active=snapshot?.active??true;
  const connection=state.connections.find(row=>row.id===selected);
  const blocked=active||current.loading||!snapshot||state.loading||state.loadingMore||state.saving||Boolean(state.pending);
  return <section className="space-y-3 rounded-3xl border border-gray-100 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
    <h3 className="font-semibold text-gray-900 dark:text-gray-100">{text('title')}</h3>
    <p className="text-sm leading-6 text-gray-500 dark:text-gray-400">{text('hint')}</p>
    <AgentBusinessCurrentState agentId={agentId} disabled={state.saving}/>
    {snapshot?.active&&<p className="text-sm text-amber-700 dark:text-amber-300">{text('active')}</p>}
    {state.error&&<p role="alert" className="text-sm text-red-600">{t(`businessConnections.errors.${state.error}`)}</p>}
    {state.pending&&<div className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">{text('unknown')} <button type="button" disabled={state.saving} onClick={()=>void state.recover()} className="underline">{text('recover')}</button></div>}
    <ul className="space-y-2">{state.bindings.map(binding=><li key={binding.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-800">
      <div><span className="font-medium text-gray-800 dark:text-gray-100">{state.connections.find(row=>row.id===binding.connection_id)?.name??text('missing')}</span>
        <p className="mt-1 text-xs text-gray-500">{binding.operations.map(operation=>t(`businessConnections.${operation}`)).join('、')} · {text(binding.enabled?'pendingTest':'retired')}</p></div>
      {binding.enabled&&<button type="button" disabled={blocked} onClick={()=>void state.save(binding.connection_id,binding.operations,false)} className="text-xs font-semibold text-red-600 disabled:opacity-40">{text('remove')}</button>}
    </li>)}</ul>
    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">{text('choose')}
      <select disabled={blocked} value={selected} onChange={event=>{setSelected(event.target.value);setOperations([]);}} className="mt-2 w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-700">
        <option value="">{text('placeholder')}</option>{state.connections.filter(row=>row.mode==='http').map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
      </select></label>
    {state.hasMore&&<button type="button" disabled={state.loading||state.loadingMore||state.saving} onClick={()=>void state.loadMore()} className="text-sm text-blue-600 disabled:opacity-40">{t('businessConnections.more')}</button>}
    {connection&&<div className="flex flex-wrap gap-4">{connection.capabilities.map(operation=><label key={operation} className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
      <input type="checkbox" disabled={blocked} checked={operations.includes(operation)} onChange={event=>setOperations(values=>event.target.checked?[...values,operation]:values.filter(value=>value!==operation))}/>{t(`businessConnections.${operation}`)}
    </label>)}</div>}
    <div className="flex flex-wrap items-center gap-3"><button type="button" disabled={blocked||!connection||!operations.length} onClick={()=>void state.save(selected,operations,true)} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{text('save')}</button>
      <button type="button" disabled={state.loading||state.saving} onClick={()=>void state.load(agentId)} className="text-sm text-blue-600">{t('businessConnections.refresh')}</button>
      <Link to="/ai/tools?tab=connections" className="text-sm text-blue-600">{text('manage')}</Link></div>
    <AgentBusinessTrialPanel agentId={agentId} active={active} bindings={state.bindings} connections={state.connections} disabled={state.loading||state.saving||Boolean(state.pending)}/>
  </section>;
}
