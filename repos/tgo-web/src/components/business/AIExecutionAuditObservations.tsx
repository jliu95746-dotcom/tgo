import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  aiRunsApiService, AI_EXECUTION_AUDIT_OBSERVATION_PAGE_SIZE,
} from '@/services/aiRunsApi';
import type { WorkflowBusinessQueryAuditList } from '@/types/workflow';

export default function AIExecutionAuditObservations({ executionId }: { executionId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<WorkflowBusinessQueryAuditList | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const label = 'businessScenarios.workflow.businessAuditObservations';

  useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    aiRunsApiService.listBusinessAuditObservations(
      executionId, page * AI_EXECUTION_AUDIT_OBSERVATION_PAGE_SIZE, controller.signal,
    ).then(data => {
      if (controller.signal.aborted) return;
      setResult(data);
      setStatus('ready');
    }).catch(() => {
      if (!controller.signal.aborted) setStatus('error');
    });
    return () => controller.abort();
  }, [executionId, page, reload]);

  const totalPages = Math.max(1, Math.ceil((result?.total ?? 0) / AI_EXECUTION_AUDIT_OBSERVATION_PAGE_SIZE));
  return <section className="mt-4 space-y-2 border-t border-gray-200 pt-3 text-xs dark:border-gray-700">
    <h4 className="font-medium text-gray-800 dark:text-gray-100">{t(`${label}.title`)}</h4>
    <p className="text-gray-500 dark:text-gray-400">{t(`${label}.hint`)}</p>
    {status === 'loading' && <p role="status" className="text-gray-500">{t(`${label}.loading`)}</p>}
    {status === 'error' && <>
      <p role="alert" className="text-red-600 dark:text-red-300">{t(`${label}.failedLoad`)}</p>
      <button type="button" onClick={() => setReload(value => value + 1)} className="text-blue-600 dark:text-blue-300">{t(`${label}.retry`)}</button>
    </>}
    {status === 'ready' && result && <>
      {result.total === 0 && <p className="text-gray-500">{t(`${label}.empty`)}</p>}
      {result.events.map(event => <div key={event.id} className="rounded-lg border border-gray-200 p-2 dark:border-gray-700">
        <p className="font-medium text-gray-700 dark:text-gray-200">
          {t(`businessScenarios.workflow.queryOperations.${event.operation}`)} · {t(`${label}.outcomes.${event.outcome}`)}
        </p>
        <p className="mt-1 break-all text-gray-500">{event.query_id}</p>
        <p className="text-gray-500">{t(`${label}.duration`, { ms: Math.round(event.duration_ms) })} · <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time></p>
      </div>)}
      {totalPages > 1 && <div className="flex items-center gap-3">
        <button type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)} className="text-blue-600 disabled:opacity-40 dark:text-blue-300">{t(`${label}.previous`)}</button>
        <span className="text-gray-500">{t(`${label}.page`, { current: page + 1, total: totalPages })}</span>
        <button type="button" disabled={page + 1 >= totalPages} onClick={() => setPage(value => value + 1)} className="text-blue-600 disabled:opacity-40 dark:text-blue-300">{t(`${label}.next`)}</button>
      </div>}
    </>}
  </section>;
}
