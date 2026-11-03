import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { workflowEventOwner } from '@/stores/workflowEventTestStore';
import { useWorkflowEventSubscriptionStore } from '@/stores/workflowEventSubscriptionStore';

export default function EventSourceConfiguration({ workflowId }: { workflowId: string }) {
  const { t } = useTranslation();
  const ownerKey = useAuthStore(state => workflowEventOwner(state.user));
  const publicationKey = useWorkflowStore(state => state.currentWorkflow?.id === workflowId
    ? `${state.currentWorkflow.publication_revision}:${state.currentWorkflow.status}` : '');
  const store = useWorkflowEventSubscriptionStore();
  const { activate } = store;
  useEffect(() => {
    if (ownerKey) return activate(ownerKey, workflowId);
    return undefined;
  }, [activate, ownerKey, workflowId, publicationKey]);
  if (!ownerKey || store.ownerKey !== ownerKey || store.workflowId !== workflowId) return null;
  const { subscription, context, deliveries } = store;
  const supported = context?.event_type === 'staff.session.closed';
  const matches = Boolean(supported && subscription?.release_id === context?.release_id
    && subscription?.release_digest === context?.release_digest);
  const needsRebind = Boolean(subscription?.enabled && context && !matches);
  const busy = store.loading || store.saving;
  const button = 'rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 disabled:opacity-40 dark:border-gray-700 dark:text-gray-300';
  return <section aria-label={t('workflowEvents.subscription.title')} className="space-y-3 rounded-2xl border border-blue-100 bg-blue-50/40 p-4 dark:border-blue-900/50 dark:bg-blue-900/10">
    <div className="flex items-center justify-between gap-2">
      <h4 className="text-sm font-bold text-gray-800 dark:text-gray-100">{t('workflowEvents.subscription.title')}</h4>
      {subscription && <span role="status" className="text-xs text-blue-600 dark:text-blue-400">{t(subscription.enabled ? (matches ? 'workflowEvents.subscription.enabled' : 'workflowEvents.subscription.needsCheck') : 'workflowEvents.subscription.disabled')}</span>}
    </div>
    <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowEvents.subscription.source')}</p>
    <p className="text-xs leading-5 text-gray-500">{t('workflowEvents.subscription.scope')}</p>
    {subscription?.version != null && <p className="text-xs text-gray-600 dark:text-gray-300">{t('workflowEvents.subscription.boundVersion', { version: subscription.version })}</p>}
    {context && supported && <p className="text-xs text-gray-600 dark:text-gray-300">{t('workflowEvents.subscription.currentVersion', { version: context.version })}</p>}
    {needsRebind && <p role="status" className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t('workflowEvents.subscription.releaseChanged')}</p>}
    {!supported && <p className="text-xs leading-5 text-gray-500">{t('workflowEvents.subscription.selectSource')}</p>}
    {store.contextError && <p className="break-words text-xs leading-5 text-amber-700 dark:text-amber-300">{store.contextError}</p>}
    {store.error && <p role="alert" className="break-words text-xs leading-5 text-red-600 dark:text-red-400">{store.error}</p>}
    <div className="flex flex-wrap gap-2">
      {supported && subscription && (!subscription.enabled || !matches) && <button type="button" disabled={busy} onClick={() => void store.setEnabled(true)} className="rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{t(subscription.enabled ? 'workflowEvents.subscription.reenable' : 'workflowEvents.subscription.enable')}</button>}
      {subscription?.enabled && <button type="button" disabled={busy} onClick={() => void store.setEnabled(false)} className={button}>{t('workflowEvents.subscription.disable')}</button>}
      <button type="button" disabled={busy} onClick={() => void store.refresh()} className={`${button} inline-flex items-center gap-2`}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}{t('workflowEvents.subscription.refresh')}</button>
    </div>
    <p className="text-[11px] leading-5 text-gray-500">{t('workflowEvents.subscription.stopHint')}</p>
    <details className="border-t border-blue-100 pt-3 dark:border-blue-900/50">
      <summary className="cursor-pointer text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowEvents.subscription.deliveries')}</summary>
      <p className="mt-2 text-[11px] leading-5 text-gray-500">{t('workflowEvents.subscription.deliveryHint')}</p>
      {store.deliveryError && <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">{store.deliveryError}</p>}
      {!store.loading && !store.deliveryError && deliveries.length === 0 && <p className="mt-2 text-xs text-gray-500">{t('workflowEvents.subscription.empty')}</p>}
      <ul className="mt-3 space-y-3">{deliveries.map(delivery => <li key={delivery.id} className="space-y-1 rounded-xl border border-gray-100 bg-white/60 p-3 text-xs dark:border-gray-700 dark:bg-gray-800/30">
        <p className="font-semibold text-gray-700 dark:text-gray-200">{t(`workflowEvents.subscription.deliveryStatus.${delivery.status}`, { defaultValue: delivery.status })}</p>
        <p className="break-all text-gray-500">{t('workflowEvents.subscription.session')}：{delivery.session_id}</p>
        <p className="text-gray-500">{delivery.created_at} · {t('workflowEvents.subscription.attempts', { count: delivery.attempts })}</p>
        {delivery.last_error && <p className="break-words text-red-600 dark:text-red-400">{t(`workflowEvents.subscription.errors.${delivery.last_error}`, { defaultValue: t('workflowEvents.subscription.unknownError', { code: delivery.last_error }) })}</p>}
        {delivery.execution_id && <p className="break-all text-gray-500">{t('workflowEvents.runId')}：{delivery.execution_id}</p>}
      </li>)}</ul>
    </details>
  </section>;
}
