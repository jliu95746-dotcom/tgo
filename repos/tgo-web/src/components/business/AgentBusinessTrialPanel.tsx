import {useEffect,useId,useState} from 'react';
import {useTranslation} from 'react-i18next';
import {Link} from 'react-router-dom';
import {Loader2,RotateCw} from 'lucide-react';
import {useAuthStore} from '@/stores/authStore';
import {useAgentBusinessTrialStore} from '@/stores/agentBusinessTrialStore';
import type {AgentBusinessConnection} from '@/types/agentBusinessConnections';
import type {BusinessCapability,BusinessConnection} from '@/types/businessConnections';
import AgentBusinessReviewForm from './AgentBusinessReviewForm';
import AgentBusinessReviewRecovery from './AgentBusinessReviewRecovery';
import {useAgentBusinessCurrentStateStore} from '@/stores/agentBusinessCurrentStateStore';

interface Props {agentId:string;active:boolean;bindings:AgentBusinessConnection[];connections:BusinessConnection[];disabled:boolean}
const field='mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
function PrivateTestForm({agentId,bindings,connections,disabled}:Props){
  const {t}=useTranslation(),label=useId(),state=useAgentBusinessTrialStore();
  const currentState=useAgentBusinessCurrentStateStore();
  const snapshot=currentState.agentId===agentId?currentState.snapshot:null;
  const active=snapshot?.active??true;
  const [choice,setChoice]=useState(''),[order,setOrder]=useState(''),[assertion,setAssertion]=useState('');
  const choices=bindings.filter(row=>row.enabled).flatMap(row=>row.operations.map(operation=>({id:`${row.connection_id}:${operation}`,connection:row.connection_id,operation})));
  const current=choices.find(row=>row.id===choice),connection=connections.find(row=>row.id===current?.connection);
  const needsOrder=current?.operation==='order_query'||current?.operation==='logistics_query';
  const text=(key:string)=>t(`businessConnections.trial.${key}`);
  const terminated=state.requestState?.status==='failed'||state.requestState?.status==='cancelled';
  const blocked=disabled||active||currentState.loading||!snapshot||state.loading||state.sending||state.reviewing||Boolean(state.pendingReview)||state.unknown||Boolean(state.pointer&&(!state.report&&!terminated||state.report?.status==='running'));
  const start=()=>{if(!current)return;const values={order:order.trim(),assertion:assertion.trim()};setOrder('');setAssertion('');void state.start(current.connection,current.operation as BusinessCapability,values.order,values.assertion);};
  return <div className="space-y-3">
    {snapshot?.active&&<p className="text-xs text-amber-700 dark:text-amber-300">{text('pauseFirst')}</p>}
    <fieldset disabled={blocked} className="space-y-3">
      <label htmlFor={`${label}-operation`} className="block text-sm text-gray-700 dark:text-gray-200">{text('operation')}
        <select id={`${label}-operation`} value={choice} onChange={event=>{setChoice(event.target.value);setOrder('');setAssertion('');}} className={field}>
          <option value="">{text('choose')}</option>{choices.map(item=><option key={item.id} value={item.id}>{connections.find(row=>row.id===item.connection)?.name??text('missingConnection')} · {t(`businessConnections.${item.operation}`)}</option>)}
        </select>
      </label>
      {current&&<>
        {needsOrder&&<label htmlFor={`${label}-order`} className="block text-sm text-gray-700 dark:text-gray-200">{text('order')}
          <input id={`${label}-order`} value={order} maxLength={64} autoComplete="off" onChange={event=>setOrder(event.target.value)} className={field}/>
        </label>}
        <label htmlFor={`${label}-customer`} className="block text-sm text-gray-700 dark:text-gray-200">{text('customer')}
          <input id={`${label}-customer`} type="password" autoComplete="off" value={assertion} maxLength={8192} onChange={event=>setAssertion(event.target.value)} className={field}/>
          <span className="mt-2 block text-xs leading-6 text-gray-500">{text('customerHint')}</span>
        </label>
        {(!connection?.enabled||connection.test_status!=='passed')&&<p className="text-xs text-amber-700 dark:text-amber-300">{text('connectionNotReady')}</p>}
      </>}
    </fieldset>
    <div className="flex flex-wrap gap-3"><button type="button" disabled={blocked||!current||needsOrder&&!order.trim()||!assertion.trim()||!connection?.enabled||connection.test_status!=='passed'} onClick={start} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{text('start')}</button>
      <Link to="/ai/tools?tab=connections" className="self-center text-xs text-blue-600 dark:text-blue-300">{text('prepare')}</Link></div>
    {!choices.length&&<p className="text-xs text-gray-500">{text('noBinding')}</p>}
    <span className="sr-only">{agentId}</span>
  </div>;
}

export default function AgentBusinessTrialPanel(props:Props){
  const {t}=useTranslation(),user=useAuthStore(state=>state.user),state=useAgentBusinessTrialStore();
  const {select,reset}=state,identity=user?.role==='admin'?`${user.project_id}:${user.id}`:null;
  useEffect(()=>{if(identity)void select(props.agentId);return()=>reset();},[identity,props.agentId,select,reset]);
  const text=(key:string)=>t(`businessConnections.trial.${key}`);
  if(!identity||state.identity!==identity||state.agentId!==props.agentId)return null;
  const busy=state.loading||state.sending||state.reviewing,report=state.report;
  const outcome=report?.status==='completed'&&report.receipt?.outcome==='failed'?'failed':report?.status;
  return <section className="mt-5 space-y-4 border-t border-gray-100 pt-5 dark:border-gray-800">
    <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-semibold text-gray-900 dark:text-white">{text('title')}</h4>
      {state.pointer&&<button type="button" disabled={busy} onClick={()=>void state.refresh()} className="flex items-center gap-1 text-xs text-blue-600 disabled:opacity-40 dark:text-blue-300"><RotateCw size={14}/>{text('refresh')}</button>}
    </div>
    <p className="text-xs leading-6 text-gray-500 dark:text-gray-400">{text('hint')}</p>
    <PrivateTestForm key={`${identity}:${props.agentId}`} {...props}/>
    {busy&&<p aria-live="polite" className="flex items-center gap-2 text-sm text-blue-600"><Loader2 size={16} className="animate-spin"/>{text('working')}</p>}
    {state.error&&<p role="alert" className="text-sm text-amber-700 dark:text-amber-300">{text(`errors.${state.error}`)}</p>}
    {state.unknown&&<p role="status" className="rounded-xl bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{text('unknown')}</p>}
    {state.pointer&&!report&&<div className="space-y-2 rounded-xl bg-gray-50 p-3 text-xs leading-6 text-gray-700 dark:bg-gray-800 dark:text-gray-200">
      {state.requestState&&<p role="status">{text(`preparation.${state.requestState.status}`)}</p>}
      {state.requestState?.error_code&&<p>{text(`preparationErrors.${state.requestState.error_code}`)}</p>}
      {state.requestState?.status!=='cancelled'&&state.requestState?.status!=='failed'&&<button type="button" disabled={busy||props.disabled} onClick={()=>void state.cancel()} className="font-medium text-red-600 disabled:opacity-40">{text('cancelOriginal')}</button>}
    </div>}
    {report&&<article className="space-y-3 rounded-2xl bg-gray-50 p-4 text-sm dark:bg-gray-800">
      <div className="flex flex-wrap justify-between gap-2"><h5 className="font-medium text-gray-800 dark:text-gray-100">{t(`businessConnections.${report.operation}`)} · {text(`status.${outcome}`)}</h5><span className="text-xs text-gray-500">{new Date(report.created_at).toLocaleString('zh-CN')}</span></div>
      {report.receipt?.summary&&<dl className="space-y-1 text-gray-700 dark:text-gray-200"><div className="flex flex-wrap gap-2"><dt>{text('result')}</dt><dd>{report.receipt.summary.status}</dd></div>
        {report.receipt.summary.carrier&&<div className="flex flex-wrap gap-2"><dt>{text('carrier')}</dt><dd>{report.receipt.summary.carrier}</dd></div>}
        {report.receipt.summary.tracking_no_masked&&<div className="flex flex-wrap gap-2"><dt>{text('tracking')}</dt><dd>{report.receipt.summary.tracking_no_masked}</dd></div>}
        {report.receipt.summary.segment&&<div className="flex flex-wrap gap-2"><dt>{text('segment')}</dt><dd>{report.receipt.summary.segment}</dd></div>}
        {(report.receipt.summary.service_tags?.length??0)>0&&<div className="flex flex-wrap gap-2"><dt>{text('serviceTags')}</dt><dd>{report.receipt.summary.service_tags?.join('、')}</dd></div>}
        {report.receipt.summary.tier&&<div className="flex flex-wrap gap-2"><dt>{text('tier')}</dt><dd>{report.receipt.summary.tier}</dd></div>}
        {report.receipt.summary.points_balance!==null&&<div className="flex flex-wrap gap-2"><dt>{text('points')}</dt><dd>{report.receipt.summary.points_balance}</dd></div>}
      </dl>}
      {report.error_code&&<p className="text-xs text-amber-700 dark:text-amber-300">{t(`businessConnections.trial.resultErrors.${report.error_code}`,{defaultValue:text('resultUnknown')})}</p>}
      {report.status==='running'&&<button type="button" disabled={busy} onClick={()=>void state.cancel()} className="text-xs font-medium text-red-600 disabled:opacity-40">{text('cancel')}</button>}
    </article>}
    <AgentBusinessReviewRecovery disabled={busy||props.disabled}/>
    {state.review&&report&&<AgentBusinessReviewForm key={`${report.id}:${state.review.latest_review?.id??''}:${state.review.checked_at}`} state={state.review} disabled={busy||Boolean(state.pendingReview)||props.disabled}/>}
  </section>;
}
