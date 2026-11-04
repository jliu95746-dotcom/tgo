import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import type { WorkflowBusinessQueryAuditList } from '@/types/workflow';

export default function WorkflowBusinessQueryAudit({ queryId }: { queryId: string }) {
  const { t } = useTranslation();
  const projectId = useAuthStore(state => state.user?.project_id);
  const [expanded, setExpanded] = useState(false);
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{
    projectId: string;
    queryId: string;
    data: WorkflowBusinessQueryAuditList;
  } | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const label = 'businessScenarios.workflow.queryAudit';

  useEffect(() => {
    if (!expanded || !projectId) return;
    const controller = new AbortController();
    setState('loading');
    WorkflowApiService.getBusinessQueryAudits(queryId, controller.signal)
      .then(data => {
        if (controller.signal.aborted) return;
        setResult({ projectId, queryId, data });
        setState('ready');
      })
      .catch(() => {
        if (!controller.signal.aborted) setState('error');
      });
    return () => controller.abort();
  }, [expanded, projectId, queryId, reload]);

  const current = result?.projectId === projectId && result?.queryId === queryId
    ? result.data : null;
  return <div className="mt-2 space-y-2 text-xs">
    <button type="button" onClick={() => setExpanded(value => !value)} className="text-blue-600 hover:underline dark:text-blue-300">
      {t(`${label}.${expanded ? 'hide' : 'show'}`)}
    </button>
    {expanded && <div className="space-y-2">
      {state === 'loading' && <p role="status" className="text-gray-500">{t('common.loading')}</p>}
      {state === 'error' && <p role="alert" className="text-red-600 dark:text-red-300">{t(`${label}.loadFailed`)}</p>}
      {state === 'ready' && current && <>
        {current.total === 0 && <p className="text-gray-500">{t(`${label}.none`)}</p>}
        {current.events.map(event => <div key={event.id} className="rounded-lg border border-gray-200 p-2 dark:border-gray-700">
          <p className="font-medium">{t(`businessScenarios.workflow.queryOperations.${event.operation}`)} · {t(`${label}.outcomes.${event.outcome}`)}</p>
          <p className="text-gray-500">{t(`${label}.duration`, { ms: Math.round(event.duration_ms) })} · <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time></p>
          <p className="break-all text-gray-500">{t(`${label}.agent`)}：{event.agent_id}</p>
        </div>)}
        {current.total > current.events.length && <p className="text-gray-500">{t(`${label}.more`, { count: current.total - current.events.length })}</p>}
        <p className="text-gray-500 dark:text-gray-400">{t(`${label}.hint`)}</p>
      </>}
      <button type="button" disabled={state === 'loading'} onClick={() => setReload(value => value + 1)} className="text-blue-600 disabled:opacity-40 dark:text-blue-300">
        {t('common.refresh')}
      </button>
    </div>}
  </div>;
}
