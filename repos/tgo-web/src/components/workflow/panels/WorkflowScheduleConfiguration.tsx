import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw } from 'lucide-react';
import { useWorkflowScheduleStore } from '@/stores/workflowScheduleStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useAuthStore } from '@/stores/authStore';
import { WorkflowApiService } from '@/services/workflowApi';
import { workflowEventOwner } from '@/stores/workflowEventTestStore';

export function scheduleTime(value: string, zone: string, language: string): string {
  try { return new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short', timeZone: zone }).format(new Date(value)); }
  catch { return value; }
}
export default function WorkflowScheduleConfiguration() {
  const { t, i18n } = useTranslation();
  const store = useWorkflowScheduleStore();
  const dirty = useWorkflowStore(state => state.isDirty);
  const executing = useWorkflowStore(state => state.isExecuting);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState('');
  const { state, history } = store, plan = state?.schedule, release = state?.current_release;
  const busy = store.loading || store.saving;
  const blocked = busy || Boolean(store.pending);
  const zone = plan?.timezone || release?.timezone || 'UTC';
  const status = !plan ? 'empty' : !plan.enabled ? 'disabled' : state?.suspension_reason ? 'suspended' : !state.binding_current ? 'changed' : 'enabled';
  const button = 'rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 disabled:opacity-40 dark:border-gray-700 dark:text-gray-300';
  async function inspect(id: string) {
    const workflowId = store.workflowId, owner = store.ownerKey;
    if (useWorkflowStore.getState().isExecuting) return;
    setOpening(true); setOpenError('');
    try {
      const run = await WorkflowApiService.getExecution(id);
      if (workflowEventOwner(useAuthStore.getState().user) !== owner
        || useWorkflowStore.getState().currentWorkflow?.id !== workflowId
        || useWorkflowStore.getState().isExecuting) return;
      if (run.id !== id || run.workflow_id !== workflowId) throw new Error(t('workflowSchedule.invalidResponse'));
      useWorkflowStore.setState({ currentExecution: run, isDebugPanelOpen: true,
        executionPanelMode: 'history', executionError: null,
        nodeExecutionMap: Object.fromEntries(run.node_executions.map(node => [node.node_id, node])) });
    } catch (error) { setOpenError(error instanceof Error ? error.message : t('workflowSchedule.loadFailed')); }
    finally { setOpening(false); }
  }
  return <section aria-label={t('workflowSchedule.title')} className="space-y-3 rounded-2xl border border-blue-100 bg-blue-50/40 p-4 dark:border-blue-900/50 dark:bg-blue-900/10">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-bold text-gray-800 dark:text-gray-100">{t('workflowSchedule.title')}</h4>
      <span role="status" className="text-xs text-blue-600 dark:text-blue-400">{t(`workflowSchedule.statuses.${status}`)}</span></div>
    {release ? <p className="text-xs text-gray-600 dark:text-gray-300">{t('workflowSchedule.publication', { version: release.version })}</p>
      : <p className="text-xs text-amber-700 dark:text-amber-300">{t('workflowSchedule.unpublished')}</p>}
    {state && !state.execution_available && (!state.runtime || state.runtime.ready) && <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t('workflowSchedule.disabledCapability')}</p>}
    {state && <div className="space-y-2 rounded-xl border border-gray-100 bg-white/60 p-3 dark:border-gray-700 dark:bg-gray-800/30">
      <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowSchedule.runtimeTitle')}</p>
      <p role="status" className={`text-xs leading-5 ${state.runtime?.ready ? 'text-green-700 dark:text-green-300' : 'text-amber-700 dark:text-amber-300'}`}>{state.runtime ? t(`workflowSchedule.runtimeStatus.${state.runtime.status}`) : t('workflowSchedule.runtimeUnknown')}</p>
      <p className="text-[11px] leading-5 text-gray-500">{t('workflowSchedule.runtimeHelp')}</p>
      {state.runtime && <><p className="text-[11px] text-gray-500">{t('workflowSchedule.runtimeChecked')}：{scheduleTime(state.runtime.checked_at, zone, i18n.language)}</p>
        <details className="text-xs text-gray-600 dark:text-gray-300"><summary className="cursor-pointer">{t('workflowSchedule.runtimeDetails')}</summary><dl className="mt-2 space-y-1">
          <div className="flex flex-wrap justify-between gap-2"><dt>{t('workflowSchedule.scheduler')}</dt><dd>{t(state.runtime.scheduler_recent ? 'workflowSchedule.recent' : 'workflowSchedule.waiting')}</dd></div>
          <div className="flex flex-wrap justify-between gap-2"><dt>{t('workflowSchedule.worker')}</dt><dd>{t(state.runtime.worker_recent ? 'workflowSchedule.recent' : 'workflowSchedule.waiting')}</dd></div>
          <div className="flex flex-wrap justify-between gap-2"><dt>{t('workflowSchedule.database')}</dt><dd>{t(state.runtime.database_ready ? 'workflowSchedule.checked' : state.runtime.status === 'database_unavailable' ? 'workflowSchedule.notReady' : 'workflowSchedule.waiting')}</dd></div>
        </dl></details></>}
    </div>}
    {plan && <div className="space-y-1 rounded-xl bg-white/70 p-3 text-xs dark:bg-gray-800/50">
      <p className="font-semibold text-gray-600 dark:text-gray-300">{t('workflowSchedule.registered')}</p>
      <p className="break-all font-mono text-gray-700 dark:text-gray-200">{plan.cron_expression} · {plan.timezone}</p>
      {plan.next_run_at && <p className="text-gray-600 dark:text-gray-300">{t('workflowSchedule.next')}：{scheduleTime(plan.next_run_at, zone, i18n.language)}</p>}
    </div>}
    {state?.suspension_reason && <p className="break-all text-xs text-amber-700 dark:text-amber-300">{t('workflowSchedule.reason')}：{t(`workflowSchedule.reasons.${state.suspension_reason}`, { defaultValue: state.suspension_reason })}</p>}
    {store.error && <p role="alert" className="break-words text-xs text-red-600 dark:text-red-400">{store.error}</p>}
    {store.notice && <p role="status" className="text-xs text-blue-600 dark:text-blue-300">{store.notice}</p>}
    {store.pending && <div className="space-y-2 rounded-xl border border-amber-200 p-3 dark:border-amber-800">
      <p role="status" className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t('workflowSchedule.pending')}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} disabled={busy} onClick={() => void store.recover()}>{t('workflowSchedule.recover')}</button>
        <button type="button" className={button} disabled={busy} onClick={() => void store.retry()}>{t('workflowSchedule.retry')}</button>
        <button type="button" className={button} disabled={busy} onClick={() => { if (window.confirm(t('workflowSchedule.forgetConfirm'))) void store.forget(); }}>{t('workflowSchedule.forget')}</button>
      </div>
    </div>}
    <div className="flex flex-wrap gap-2">
      {!plan?.enabled && <button type="button" disabled={blocked || !release || dirty} onClick={() => void store.save(false)} className={button}>{t('workflowSchedule.saveDisabled')}</button>}
      <button type="button" disabled={blocked || !release || !state?.execution_available || !state?.runtime?.ready || dirty || Boolean(plan?.enabled && state.binding_current && !state.suspension_reason)} onClick={() => void store.save(true)} className="rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{t('workflowSchedule.enable')}</button>
      {plan?.enabled && <button type="button" disabled={blocked} onClick={() => void store.disable()} className={button}>{t('workflowSchedule.disable')}</button>}
      <button type="button" disabled={busy} onClick={() => void store.refresh()} className={`${button} inline-flex items-center gap-2`}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}{t('workflowSchedule.refresh')}</button>
    </div>
    <p className="text-[11px] leading-5 text-gray-500">{t('workflowSchedule.stopHint')}</p>
    <details className="border-t border-blue-100 pt-3 dark:border-blue-900/50"><summary className="cursor-pointer text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowSchedule.history')}</summary>
      <p className="mt-2 text-[11px] leading-5 text-gray-500">{t('workflowSchedule.historyHint')}</p>
      <p className="mt-1 text-[11px] leading-5 text-gray-500">{t('workflowSchedule.historyZone', { timezone: zone })}</p>
      {(store.historyError || openError) && <p role="alert" className="mt-2 text-xs text-red-600">{store.historyError || openError}</p>}
      {history && history.items.length === 0 && <p className="mt-3 text-xs text-gray-500">{t('workflowSchedule.emptyHistory')}</p>}
      <ul className="mt-3 space-y-3">{history?.items.map(row => <li key={row.id} className="space-y-1 rounded-xl border border-gray-100 bg-white/60 p-3 text-xs dark:border-gray-700 dark:bg-gray-800/30">
        <p className="font-semibold text-gray-700 dark:text-gray-200">{scheduleTime(row.scheduled_at, zone, i18n.language)} · {t(`workflowSchedule.source.${row.status}`)}</p>
        {row.execution_status && <p className="text-gray-600 dark:text-gray-300">{t('workflowSchedule.run')}：{t(`workflowSchedule.runStatus.${row.execution_status}`, { defaultValue: row.execution_status })}</p>}
        <p className="text-gray-500">{t('workflowSchedule.revision', { revision: row.revision })} · {t('workflowSchedule.attempts', { count: row.attempts })}</p>
        {(row.error_code || row.last_failure_code) && <p className="break-all text-amber-700 dark:text-amber-300">{t(row.error_code ? 'workflowSchedule.reason' : 'workflowSchedule.lastFailure')}：{t(`workflowSchedule.reasons.${row.error_code || row.last_failure_code}`, { defaultValue: row.error_code || row.last_failure_code || '' })}</p>}
        {row.execution_id && <button className="text-blue-600 underline disabled:opacity-40 dark:text-blue-400" type="button" disabled={opening || executing} onClick={() => void inspect(row.execution_id!)}>{t('workflowSchedule.inspect')}</button>}
      </li>)}</ul>
      {history && <div className="mt-3 flex flex-wrap items-center gap-2"><button type="button" className={button} disabled={busy || history.offset === 0} onClick={() => void store.refresh(Math.max(0, history.offset - history.limit))}>{t('workflowSchedule.previous')}</button>
        <button type="button" className={button} disabled={busy || !history.has_more} onClick={() => void store.refresh(history.offset + history.limit)}>{t('workflowSchedule.nextPage')}</button><span className="text-xs text-gray-500">{t('workflowSchedule.total', { count: history.total })}</span></div>}
    </details>
  </section>;
}
