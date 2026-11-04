import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { aiRunsApiService, AI_EXECUTION_TOOL_EVENT_PAGE_SIZE } from '@/services/aiRunsApi';
import type { AIExecutionToolEventSummary } from '@/types/aiRuns';

export default function AIExecutionToolEventList({ executionId, includeCallId = false }: {
  executionId: string;
  includeCallId?: boolean;
}) {
  const { t } = useTranslation();
  const [records, setRecords] = useState<AIExecutionToolEventSummary[]>([]);
  const [pageOffset, setPageOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const label = 'businessScenarios.workflow.toolEvents';

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    aiRunsApiService.listToolEvents(executionId, pageOffset, controller.signal)
      .then(data => {
        if (controller.signal.aborted) return;
        setRecords(previous => pageOffset === 0
          ? data
          : [...previous, ...data.filter(item =>
            !previous.some(record => record.sequence_no === item.sequence_no))]);
        setHasMore(data.length === AI_EXECUTION_TOOL_EVENT_PAGE_SIZE);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [executionId, pageOffset]);

  return <div className="mt-3 space-y-2 border-l-2 border-blue-200 pl-3 dark:border-blue-800">
    <p className="text-xs text-gray-500 dark:text-gray-400">{t(`${label}.hint`)}</p>
    {records.map(item => <div key={item.sequence_no} className="text-xs text-gray-600 dark:text-gray-300">
      <span>{item.sequence_no}. {item.tool_name} · {t(`${label}.status.${item.status}`)}</span>
      <span className="ml-2 text-gray-500">{new Date(item.occurred_at).toLocaleString()}</span>
      {includeCallId && item.tool_call_id && <p className="break-all text-gray-500">{item.tool_call_id}</p>}
    </div>)}
    {loading && <p role="status" className="text-xs text-gray-500">{t(`${label}.loading`)}</p>}
    {error && <p role="alert" className="text-xs text-red-600">{t(`${label}.failedLoad`)}</p>}
    {!loading && !error && records.length === 0 && <p className="text-xs text-gray-500">{t(`${label}.empty`)}</p>}
    {hasMore && !error && <button type="button" disabled={loading} onClick={() => setPageOffset(offset => offset + AI_EXECUTION_TOOL_EVENT_PAGE_SIZE)} className="text-xs text-blue-600 disabled:opacity-50 dark:text-blue-300">
      {t(`${label}.loadMore`)}
    </button>}
  </div>;
}
