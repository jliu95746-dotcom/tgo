import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw, X } from 'lucide-react';
import type { BusinessCapability, BusinessConnection, BusinessConnectionTestReport } from '@/types/businessConnections';
import { useBusinessConnectionStore } from '@/stores/businessConnectionStore';
import { useDialogFocus } from '@/hooks/useDialogFocus';

function ReportCard({ report }: { report: BusinessConnectionTestReport }) {
  const {t}=useTranslation();
  const prefix='businessConnections.';
  const validity=report.applicability;
  const expired=validity?.valid_until ? new Date(validity.valid_until).getTime()<=Date.now() : false;
  const status=expired&&validity?.status==='current'?'stale':validity?.status??'unavailable';
  const issues=expired&&validity?.status==='current'?['expired']:validity?.issues??['evidence_unknown'];
  return <article className="rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><h4 className="font-semibold text-gray-800 dark:text-gray-100">{t(prefix+report.operation)}</h4><span className={status==='current'?'text-emerald-600':report.status==='failed'?'text-red-600':'text-amber-700 dark:text-amber-300'}>{t(`${prefix}applicability.${status}`)}</span></div>
    <p className="mt-2 text-xs text-gray-500">{t(`${prefix}state.${report.status}`)} · {t(`${prefix}configurationRevision`,{revision:report.configuration_revision})} · {new Date(report.created_at).toLocaleString()}</p>
    {report.mode==='demo'&&<p className="mt-3 text-xs leading-5 text-blue-600 dark:text-blue-300">{t(`${prefix}demoHint`)}</p>}
    {report.summary&&<div className="mt-3 rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-800"><p className="mb-1 text-xs text-gray-500">{t(`${prefix}historical`)}</p><p className="text-gray-800 dark:text-gray-200">{report.summary.status}</p>{report.summary.carrier&&<p className="mt-1 text-gray-600 dark:text-gray-300">{report.summary.carrier} · {report.summary.tracking_no_masked}</p>}{report.summary.amount_minor!==null&&report.summary.amount_minor!==undefined&&<p className="mt-1 text-gray-600 dark:text-gray-300">{(report.summary.amount_minor/100).toFixed(2)} {report.summary.currency}</p>}{report.summary.segment&&<p className="mt-1 text-gray-600 dark:text-gray-300">{t(`${prefix}segment`)}：{report.summary.segment}</p>}{report.summary.service_tags?.length? <p className="mt-1 text-gray-600 dark:text-gray-300">{t(`${prefix}service_tags`)}：{report.summary.service_tags.join('、')}</p>:null}{report.summary.tier&&<p className="mt-1 text-gray-600 dark:text-gray-300">{t(`${prefix}tier`)}：{report.summary.tier}</p>}{report.summary.points_balance!==null&&report.summary.points_balance!==undefined&&<p className="mt-1 text-gray-600 dark:text-gray-300">{t(`${prefix}points_balance`)}：{report.summary.points_balance}</p>}</div>}
    {validity?.valid_until&&<p className="mt-3 text-xs text-gray-500">{t(`${prefix}validUntil`,{time:new Date(validity.valid_until).toLocaleString()})}</p>}
    {issues.length>0&&<ul className="mt-3 space-y-1 text-xs text-amber-700 dark:text-amber-300">{issues.map(issue=><li key={issue}>{t(`${prefix}reasons.${issue}`)}</li>)}</ul>}
    {report.error_code&&<p role="status" className="mt-3 text-xs text-red-600">{t(`${prefix}testErrors.${report.error_code}`,{defaultValue:t(`${prefix}errors.unavailable`)})}</p>}
  </article>;
}

export default function ConnectionTestDialog({connection,onClose}:{connection:BusinessConnection;onClose:()=>void}) {
  const {t}=useTranslation();
  const state=useBusinessConnectionStore();
  const [operation,setOperation]=useState<BusinessCapability>(connection.capabilities[0]);
  const [order,setOrder]=useState(connection.mode==='demo'?'DEMO-ORDER-001':'');
  const [customer,setCustomer]=useState(connection.mode==='demo'?'demo-customer':'');
  const [invalid,setInvalid]=useState(false);
  const pending=state.pendingTests[connection.id];
  const dialog=useDialogFocus<HTMLDivElement>(onClose);
  const text=(key:string)=>t(`businessConnections.${key}`);
  const start=()=>{
    const needsOrder=operation==='order_query'||operation==='logistics_query';
    const valid=(!needsOrder||/^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$/.test(order))&&/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$/.test(customer);
    setInvalid(!valid);
    if(valid)void state.startTest(operation,order,customer,connection.revision);
  };
  return <div className="fixed inset-0 z-[90] flex items-center justify-center bg-gray-900/50 p-4 backdrop-blur-sm" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="connection-test-title" className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl dark:bg-gray-900 sm:p-8">
      <header className="flex items-center justify-between gap-3"><div><h3 id="connection-test-title" className="text-xl font-bold text-gray-900 dark:text-white">{text('testTitle')} · {connection.name}</h3><p className="mt-2 text-sm text-gray-500">{text('testHint')}</p></div><button onClick={onClose} aria-label={text('close')} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><X size={20}/></button></header>
      {connection.mode==='demo'&&<p className="mt-5 rounded-2xl bg-blue-50 p-4 text-sm leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{text('demoHint')}</p>}
      {state.error&&<p role="alert" className="mt-4 text-sm text-red-600">{text(`errors.${state.error}`)}</p>}
      {!state.storageAvailable&&<p role="alert" className="mt-4 text-sm text-amber-700">{text('storageUnavailable')}</p>}
      <form className="mt-5 space-y-4" onSubmit={event=>{event.preventDefault();start();}}>
        <fieldset disabled={Boolean(pending)||state.submitting||state.busy} className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm text-gray-700 dark:text-gray-200">{text('capabilities')}<select value={operation} onChange={event=>setOperation(event.target.value as BusinessCapability)} className="mt-2 w-full rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">{connection.capabilities.map(value=><option key={value} value={value}>{text(value)}</option>)}</select></label>
          {(operation==='order_query'||operation==='logistics_query')&&<label className="text-sm text-gray-700 dark:text-gray-200">{text('order')}<input required maxLength={64} value={order} onChange={event=>setOrder(event.target.value)} className="mt-2 w-full rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800"/></label>}
          <label className="text-sm text-gray-700 dark:text-gray-200 sm:col-span-2">{text('customer')}<input required maxLength={128} value={customer} onChange={event=>setCustomer(event.target.value)} className="mt-2 w-full rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800"/><span className="mt-2 block text-xs text-gray-500">{text('testInputHint')}</span></label>
        </fieldset>
        {invalid&&<p role="alert" className="text-sm text-red-600">{text('formErrors.test')}</p>}
        <div className="flex flex-wrap items-center justify-end gap-3">{pending?<>
          <p role="status" className="mr-auto text-xs text-amber-700 dark:text-amber-300">{text(pending.unknown?'unknown':'running')}</p>
          <button type="button" disabled={state.cancelling} onClick={()=>void state.recoverTest()} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 dark:border-gray-700 dark:text-gray-200">{text('queryOriginal')}</button>
          {pending.body&&pending.unknown&&<button type="button" disabled={state.submitting||state.cancelling} onClick={()=>void state.retryTest()} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 dark:border-gray-700 dark:text-gray-200">{text('retryOriginal')}</button>}
          <button type="button" disabled={state.cancelling} onClick={()=>void state.cancelTest()} className="rounded-xl border border-red-200 px-4 py-2 text-sm text-red-600">{text('cancelTest')}</button>
        </>:<button disabled={state.submitting||state.busy} className="flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{state.submitting&&<Loader2 size={16} className="animate-spin"/>}{text('start')}</button>}</div>
      </form>
      <p className="mt-4 text-xs leading-5 text-gray-500">{text('pointerOnly')}</p>
      <section className="mt-7 space-y-3"><div className="flex items-center justify-between"><h4 className="font-bold text-gray-900 dark:text-white">{text('history')}</h4><button disabled={state.historyLoading} onClick={()=>void state.refreshHistory()} className="flex items-center gap-2 rounded-lg p-2 text-xs text-blue-600"><RefreshCw size={14} className={state.historyLoading?'animate-spin':''}/>{text('refreshReport')}</button></div>{state.historyLoading&&<Loader2 className="animate-spin text-blue-600" size={20}/>} {!state.historyLoading&&!state.reports.length&&<p className="rounded-xl bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800">{text('noHistory')}</p>}{state.reports.filter(report=>report.connection_id===connection.id).map(report=><ReportCard key={report.id} report={report}/>)}</section>
      <p className="mt-5 text-xs leading-5 text-gray-500">{text('scopeHint')}</p>
    </div>
  </div>;
}
