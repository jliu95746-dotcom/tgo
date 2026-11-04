import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  aiRunsApiService, AI_EXECUTION_BUSINESS_QUERY_PAGE_SIZE,
} from '@/services/aiRunsApi';
import type { AIExecutionAttemptSummary, AIExecutionBusinessQuerySummary } from '@/types/aiRuns';
import WorkflowBusinessQueryAudit from './WorkflowBusinessQueryAudit';
import AIExecutionAuditObservations from './AIExecutionAuditObservations';

export default function AIExecutionBusinessQueries({ executionId, attemptStatus }: {
  executionId: string;
  attemptStatus: AIExecutionAttemptSummary['status'];
}) {
  const { t } = useTranslation();
  const [records, setRecords] = useState<AIExecutionBusinessQuerySummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const label = 'businessScenarios.workflow.businessQueryLinks';

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    aiRunsApiService.listBusinessQueries(executionId, offset, controller.signal)
      .then(data => {
        if (controller.signal.aborted) return;
        setRecords(previous => offset === 0 ? data : [
          ...previous, ...data.filter(item => !previous.some(row => row.query_id === item.query_id)),
        ]);
        setHasMore(data.length === AI_EXECUTION_BUSINESS_QUERY_PAGE_SIZE);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [executionId, offset]);

  return <div className="mt-3 space-y-2 border-l-2 border-blue-200 pl-3 dark:border-blue-800">
    <p className="text-xs text-gray-500 dark:text-gray-400">{t(`${label}.hint`)}</p>
    {records.map(item => <div key={item.query_id} className="rounded-lg border border-gray-200 p-2 text-xs dark:border-gray-700">
      <p className="text-gray-700 dark:text-gray-200">
        {t(`businessScenarios.workflow.queryOperations.${item.operation}`)} · {t(`${label}.outcomes.${item.outcome}`)}
      </p>
      <p className="mt-1 break-all text-gray-500">{item.query_id}</p>
      <p className="text-gray-500">{new Date(item.created_at).toLocaleString()}</p>
      <WorkflowBusinessQueryAudit queryId={item.query_id} />
    </div>)}
    {loading && <p role="status" className="text-xs text-gray-500">{t(`${label}.loading`)}</p>}
    {error && <p role="alert" className="text-xs text-red-600">{t(`${label}.failedLoad`)}</p>}
    {!loading && !error && records.length === 0 && <p className="text-xs text-gray-500">
      {t(`${label}.${attemptStatus === 'failed' ? 'emptyFailed' : attemptStatus === 'running' ? 'emptyRunning' : 'empty'}`)}
    </p>}
    {hasMore && !error && <button type="button" disabled={loading} onClick={() => setOffset(value => value + AI_EXECUTION_BUSINESS_QUERY_PAGE_SIZE)} className="text-xs text-blue-600 disabled:opacity-40 dark:text-blue-300">
      {t(`${label}.loadMore`)}
    </button>}
    <AIExecutionAuditObservations key={executionId} executionId={executionId} />
  </div>;
}
