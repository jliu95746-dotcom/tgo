import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { useAgentDeviceScriptSelectionStore } from '@/stores/agentDeviceScriptSelectionStore';

interface Props { agentId: string; reportId: string; reviewId: string | null; approved: boolean; reviewBusy: boolean }

export default function AgentDeviceScriptSelection({ agentId, reportId, reviewId, approved, reviewBusy }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const store = useAgentDeviceScriptSelectionStore();
  const select = store.select;
  const refresh = store.refresh;
  const [expired, setExpired] = useState(false);
  const identity = user?.role === 'admin' ? `${user.project_id}:${user.id}` : null;
  const owns = store.identity === identity && store.agentId === agentId;
  const current = owns ? store.current : null;
  const pending = owns ? store.pending : null;
  const record = current?.selection;
  const blocked = !owns || store.busy || store.loading || reviewBusy;
  useEffect(() => { void select(agentId); }, [agentId, identity, select]);
  useEffect(() => { void refresh(); }, [reportId, reviewId, approved, refresh]);
  useEffect(() => {
    setExpired(Boolean(record && Date.parse(record.expires_at) <= Date.now()));
    if (!record || current?.status !== 'selected') return;
    const timer = window.setTimeout(() => setExpired(true), Math.max(0, Date.parse(record.expires_at) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [record, current?.status]);
  if (!user || user.role !== 'admin') return null;
  const text = (name: string) => t(`agentDeviceScripts.selection.${name}`);
  const same = record?.enabled && record.report_id === reportId && record.review_id === reviewId;
  const status = current?.status === 'selected' && expired ? 'expired' : current?.status;
  return <section aria-label={text('title')} className="space-y-2 border-t border-gray-200 pt-3 text-sm dark:border-gray-700">
    <h5 className="font-medium text-gray-900 dark:text-white">{text('title')}</h5>
    <p className="text-xs text-gray-500 dark:text-gray-400">{text('hint')}</p>
    {status && <p role="status">{text(`status.${status}`)}</p>}
    {record && <div className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
      <p className="break-all">{t('agentDeviceScripts.selection.source', { report: record.report_id, revision: record.revision })}</p>
      <p>{t('agentDeviceScripts.selection.expiry', { date: new Date(record.expires_at).toLocaleString() })}</p>
    </div>}
    {owns && store.error && <p role="alert" className="text-red-600">{text(`error.${store.error}`)}</p>}
    {pending ? <div className="space-y-2">
      <p className="break-all text-xs text-gray-500">{text('pending')} {pending.request_id}</p>
      <button type="button" disabled={blocked} onClick={() => void store.recover()} className="text-blue-600 disabled:opacity-40">{text('recover')}</button>
      {store.error === 'notFound' && <button type="button" disabled={blocked} onClick={() => void store.retry()} className="ml-4 text-blue-600 disabled:opacity-40">{text('retry')}</button>}
    </div> : <div className="flex flex-wrap gap-4">
      <button type="button" disabled={blocked} onClick={() => void store.refresh()} className="text-blue-600 disabled:opacity-40">{text(store.loading ? 'loading' : 'read')}</button>
      <button type="button" disabled={blocked || !current || !approved || !reviewId || Boolean(same && status === 'selected')}
        onClick={() => reviewId && void store.choose(reportId, reviewId)} className="text-blue-600 disabled:opacity-40">
        {text(same && status === 'selected' ? 'chosen' : 'choose')}
      </button>
      {record?.enabled && <button type="button" disabled={blocked} onClick={() => void store.clear()} className="text-red-600 disabled:opacity-40">{text('clear')}</button>}
    </div>}
  </section>;
}
