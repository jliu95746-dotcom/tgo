import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AIAgentsApiService } from '@/services/aiAgentsApi';
import { useAuthStore } from '@/stores/authStore';
import type { WorkflowPinTransitionHistory } from '@/types/workflowReleaseAudit';

interface Props {
  agents: ReadonlyArray<{ id: string; name: string }>;
  identity: string;
  revision: number;
}

const pageSize = 20;

export default function WorkflowPinTransitionHistoryPanel({ agents, identity, revision }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [agentId, setAgentId] = useState('');
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState<WorkflowPinTransitionHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!open || !agentId) return;
    let cancelled = false;
    const owns = () => {
      const user = useAuthStore.getState().user;
      return user?.role === 'admin' && `${user.project_id}:${user.id}` === identity;
    };
    setLoading(true);
    setError(false);
    setHistory(null);
    void AIAgentsApiService.getWorkflowPinTransitions(agentId, offset, pageSize)
      .then(result => { if (!cancelled && owns()) setHistory(result); })
      .catch(() => { if (!cancelled && owns()) setError(true); })
      .finally(() => { if (!cancelled && owns()) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId, identity, offset, open, revision]);

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <button type="button" onClick={() => setOpen(value => !value)}
        className="font-medium text-blue-700 hover:underline dark:text-blue-300">
        {t('workflowRelease.audit.historyTitle')}
      </button>
      {open && <div className="mt-3 space-y-3 text-sm">
        <select aria-label={t('workflowRelease.audit.historyAgent')} value={agentId}
          onChange={event => { setAgentId(event.target.value); setOffset(0); }}
          className="w-full max-w-sm rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100">
          <option value="">{t('workflowRelease.audit.historyAgent')}</option>
          {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
        </select>
        {loading && <p className="text-gray-500">{t('workflowRelease.audit.historyLoading')}</p>}
        {error && <p role="alert" className="text-red-700 dark:text-red-300">{t('workflowRelease.audit.historyFailed')}</p>}
        {history && <>
          {history.data.length === 0 ? <p className="text-gray-500">{t('workflowRelease.audit.historyEmpty')}</p> :
            <ul className="space-y-2">
              {history.data.map(entry => <li key={entry.id} className="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800">
                <span className="font-medium text-gray-900 dark:text-gray-100">{entry.workflow_id}</span>
                <span className="ml-2 text-gray-600 dark:text-gray-300">{t('workflowRelease.audit.historyEntry', {
                  version: entry.release_version, time: new Date(entry.created_at).toLocaleString(),
                })}</span>
              </li>)}
            </ul>}
          {(history.pagination.has_prev || history.pagination.has_next) && <div className="flex gap-3">
            <button type="button" disabled={!history.pagination.has_prev} onClick={() => setOffset(value => Math.max(0, value - pageSize))}
              className="text-blue-700 disabled:opacity-40 dark:text-blue-300">{t('workflowRelease.audit.previous')}</button>
            <button type="button" disabled={!history.pagination.has_next} onClick={() => setOffset(value => value + pageSize)}
              className="text-blue-700 disabled:opacity-40 dark:text-blue-300">{t('workflowRelease.audit.next')}</button>
          </div>}
        </>}
      </div>}
    </section>
  );
}
