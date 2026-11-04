import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkflowWebhookStore } from '@/stores/workflowWebhookStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useAuthStore } from '@/stores/authStore';
import { workflowEventOwner } from '@/stores/workflowEventTestStore';
import { WorkflowApiService } from '@/services/workflowApi';
import type { WebhookDelivery } from '@/types/workflowWebhooks';
import { scheduleTime } from './WorkflowScheduleConfiguration';

export default function WebhookDeliveryHistory() {
  const { t, i18n } = useTranslation();
  const store = useWorkflowWebhookStore();
  const executing = useWorkflowStore(state => state.isExecuting);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  async function inspect(row: WebhookDelivery) {
    const workflowId = store.workflowId, owner = store.ownerKey;
    if (!row.execution_id || useWorkflowStore.getState().isExecuting) return;
    setOpening(true); setError('');
    try {
      const run = await WorkflowApiService.getExecution(row.execution_id);
      if (workflowEventOwner(useAuthStore.getState().user) !== owner
        || useWorkflowStore.getState().currentWorkflow?.id !== workflowId
        || useWorkflowStore.getState().isExecuting) return;
      if (run.id !== row.execution_id || run.workflow_id !== workflowId || run.release_id !== row.release_id
        || run.source_channel !== 'external_webhook' || run.source_message_id !== `webhook-${row.id}`) {
        throw Error('invalid');
      }
      useWorkflowStore.setState({ currentExecution: run, isDebugPanelOpen: true,
        executionPanelMode: 'history', executionError: null,
        nodeExecutionMap: Object.fromEntries(run.node_executions.map(node => [node.node_id, node])) });
    } catch { setError(t('workflowWebhook.invalidResponse')); }
    finally { setOpening(false); }
  }
  const { history } = store;
  const button = 'rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 disabled:opacity-40 dark:border-gray-700 dark:text-gray-300';
  return <details className="border-t border-blue-100 pt-3 dark:border-blue-900/50">
    <summary className="cursor-pointer text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowWebhook.history')}</summary>
    <p className="mt-2 text-[11px] leading-5 text-gray-500">{t('workflowWebhook.historyHint')}</p>
    {(store.historyError || error) && <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">{store.historyError || error}</p>}
    {history?.items.length === 0 && <p className="mt-3 text-xs text-gray-500">{t('workflowWebhook.emptyHistory')}</p>}
    <ul className="mt-3 space-y-3">{history?.items.map(row => <li key={row.id} className="space-y-1 rounded-xl border border-gray-100 bg-white/60 p-3 text-xs dark:border-gray-700 dark:bg-gray-800/30">
      <p className="font-semibold text-gray-700 dark:text-gray-200">{scheduleTime(row.created_at, Intl.DateTimeFormat().resolvedOptions().timeZone, i18n.language)}</p>
      <p className="text-blue-600 dark:text-blue-400">{t(`workflowWebhook.source.${row.status}`)}</p>
      {row.execution_status && <p className="text-gray-600 dark:text-gray-300">{t(`workflowSchedule.runStatus.${row.execution_status}`, { defaultValue: row.execution_status })}</p>}
      <p className="text-gray-500">{t('workflowWebhook.revision', { revision: row.binding_revision })} · {t('workflowWebhook.attempts', { count: row.attempts })}</p>
      {row.error_code && <p className="break-all text-amber-700 dark:text-amber-300">{t('workflowWebhook.reason')}：<code>{row.error_code}</code></p>}
      {row.execution_id && <button type="button" disabled={opening || executing} className="text-blue-600 underline disabled:opacity-40 dark:text-blue-400" onClick={() => void inspect(row)}>{t('workflowWebhook.inspect')}</button>}
    </li>)}</ul>
    {history && <div className="mt-3 flex flex-wrap items-center gap-2">
      <button type="button" className={button} disabled={store.loading || store.saving || history.offset === 0} onClick={() => void store.refresh(Math.max(0, history.offset - history.limit))}>{t('workflowWebhook.previous')}</button>
      <button type="button" className={button} disabled={store.loading || store.saving || !history.has_more} onClick={() => void store.refresh(history.offset + history.limit)}>{t('workflowWebhook.nextPage')}</button>
      <span className="text-xs text-gray-500">{t('workflowWebhook.total', { count: history.total })}</span>
    </div>}
  </details>;
}
