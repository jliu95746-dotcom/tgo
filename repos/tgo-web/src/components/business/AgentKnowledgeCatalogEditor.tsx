import { useEffect, useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { useAgentKnowledgeCatalogStore } from '@/stores/agentKnowledgeCatalogStore';
import { useAgentTrialReviewStore } from '@/stores/agentTrialReviewStore';

const button = 'rounded-xl border border-gray-200 px-3 py-2 text-sm disabled:opacity-50 dark:border-gray-700';
export default function AgentKnowledgeCatalogEditor({ agentId, disabled }: { agentId: string; disabled: boolean }) {
  const { t } = useTranslation();
  const store = useAgentKnowledgeCatalogStore();
  const user = useAuthStore(state => state.user);
  const prefix = useId();
  const load = store.load;
  useEffect(() => {
    let mounted = true;
    void load(agentId).then(recovered => {
      if (mounted && recovered) void useAgentTrialReviewStore.getState().refresh();
    });
    return () => { mounted = false; };
  }, [agentId, user?.id, user?.project_id, user?.role, load]);
  if (store.agentId !== agentId || store.identity !== `${user?.project_id}:${user?.id}`) return null;
  const busy = store.saving || store.loading || disabled;
  return <section className="space-y-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-sm font-medium text-gray-700 dark:text-gray-200">{t('businessScenarios.catalog.title')}</span>
      {!store.editing && <button type="button" className={button} disabled={busy || !store.catalog} onClick={store.open}>{t('businessScenarios.catalog.configure')}</button>}
    </div>
    <p className="text-xs leading-6 text-gray-500 dark:text-gray-400">{t('businessScenarios.catalog.hint')}</p>
    {store.catalog && <p className="text-xs text-gray-500">{store.catalog.revision ? t('businessScenarios.catalog.version', { revision: store.catalog.revision }) : t('businessScenarios.catalog.default')}</p>}
    {store.saved && <p role="status" className="text-sm text-green-600 dark:text-green-400">{t('businessScenarios.catalog.saved')}</p>}
    {store.error && <div role="alert" className="text-sm text-red-600 dark:text-red-300"><p>{t(`businessScenarios.catalog.errors.${store.error}`)}</p>
      {!store.pending && <button type="button" disabled={busy} className={`${button} mt-2`} onClick={() => void load(agentId)}>{t('businessScenarios.catalog.reload')}</button>}
    </div>}
    {store.editing && <div className="space-y-3">{store.questions.map((question, index) => <label key={index} htmlFor={`${prefix}-${index}`} className="block text-sm text-gray-700 dark:text-gray-200">
      {t(`businessScenarios.catalog.slots.${index}`)}
      <textarea id={`${prefix}-${index}`} rows={2} maxLength={4000} value={question} disabled={busy || Boolean(store.pending)} onChange={event => store.change(index, event.target.value)} className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800" />
    </label>)}
      {store.pending && <p className="text-xs text-amber-600 dark:text-amber-300">{t('businessScenarios.catalog.unknown')}</p>}
      <div className="flex gap-2"><button type="button" disabled={busy || store.error === 'conflict'} className={`${button} border-blue-600 bg-blue-600 text-white`} onClick={() => void store.save().then(saved => { if (saved) void useAgentTrialReviewStore.getState().refresh(); })}>{t(`businessScenarios.catalog.${store.saving ? 'saving' : store.pending ? 'retry' : 'save'}`)}</button>
        <button type="button" disabled={busy || Boolean(store.pending)} className={button} onClick={store.close}>{t('businessScenarios.catalog.cancel')}</button></div>
    </div>}
  </section>;
}
