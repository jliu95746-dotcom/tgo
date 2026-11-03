import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  aiRunsApiService,
  AI_EXECUTION_ATTEMPT_PAGE_SIZE,
} from '@/services/aiRunsApi';
import type {
  AIExecutionAttemptLookup,
  AIExecutionAttemptSummary,
} from '@/types/aiRuns';
import AIExecutionDeviceSession from './AIExecutionDeviceSession';
import AIExecutionToolEventList from './AIExecutionToolEventList';
import AIExecutionBusinessQueries from './AIExecutionBusinessQueries';

interface AIExecutionAttemptHistoryProps {
  lookup: AIExecutionAttemptLookup;
}

export default function AIExecutionAttemptHistory({ lookup }: AIExecutionAttemptHistoryProps) {
  const { t } = useTranslation();
  const [records, setRecords] = useState<AIExecutionAttemptSummary[]>([]);
  const [pageOffset, setPageOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [openExecutionId, setOpenExecutionId] = useState<string | null>(null);
  const [openDeviceExecutionId, setOpenDeviceExecutionId] = useState<string | null>(null);
  const [openQueryExecutionId, setOpenQueryExecutionId] = useState<string | null>(null);

  const { channel_id: channelId, source_message_id: sourceMessageId } = lookup;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    aiRunsApiService.listChannelMessageAttempts(
      { channel_id: channelId, source_message_id: sourceMessageId },
      pageOffset,
      controller.signal,
    )
      .then(data => {
        if (controller.signal.aborted) return;
        setRecords(previous => pageOffset === 0
          ? data
          : [...previous, ...data.filter(item =>
            !previous.some(record => record.execution_id === item.execution_id))]);
        setHasMore(data.length === AI_EXECUTION_ATTEMPT_PAGE_SIZE);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [channelId, sourceMessageId, pageOffset]);

  const label = 'businessScenarios.workflow.aiAttempts';
  return <section className="space-y-3 rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
    <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t(`${label}.title`)}</h3>
    {records.map(record => <div key={record.execution_id} className="rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-900">
      <p className="text-gray-700 dark:text-gray-200">
        {new Date(record.started_at).toLocaleString()} · {t(`${label}.status.${record.status}`)}
      </p>
      <p className="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">{record.execution_id}</p>
      {record.completed_at && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
        {t(`${label}.completedAt`)}：{new Date(record.completed_at).toLocaleString()}
      </p>}
      <button type="button" onClick={() => setOpenExecutionId(current => current === record.execution_id ? null : record.execution_id)} className="mt-2 text-xs text-blue-600 hover:underline dark:text-blue-300">
        {t(`businessScenarios.workflow.toolEvents.${openExecutionId === record.execution_id ? 'hide' : 'show'}`)}
      </button>
      {openExecutionId === record.execution_id && <AIExecutionToolEventList executionId={record.execution_id} includeCallId />}
      <button type="button" onClick={() => setOpenDeviceExecutionId(current => current === record.execution_id ? null : record.execution_id)} className="ml-4 mt-2 text-xs text-blue-600 hover:underline dark:text-blue-300">
        {t(`businessScenarios.workflow.deviceExecution.${openDeviceExecutionId === record.execution_id ? 'hide' : 'show'}`)}
      </button>
      {openDeviceExecutionId === record.execution_id && <AIExecutionDeviceSession executionId={record.execution_id} />}
      <button type="button" onClick={() => setOpenQueryExecutionId(current => current === record.execution_id ? null : record.execution_id)} className="ml-4 mt-2 text-xs text-blue-600 hover:underline dark:text-blue-300">
        {t(`businessScenarios.workflow.businessQueryLinks.${openQueryExecutionId === record.execution_id ? 'hide' : 'show'}`)}
      </button>
      {openQueryExecutionId === record.execution_id && <AIExecutionBusinessQueries executionId={record.execution_id} attemptStatus={record.status} />}
    </div>)}
    {loading && <p role="status" className="text-sm text-gray-500">{t(`${label}.loading`)}</p>}
    {error && <p role="alert" className="text-sm text-red-600">{t(`${label}.failedLoad`)}</p>}
    {!loading && !error && records.length === 0 && <p className="text-sm text-gray-500">{t(`${label}.empty`)}</p>}
    {hasMore && !error && <button type="button" disabled={loading} onClick={() => setPageOffset(offset => offset + AI_EXECUTION_ATTEMPT_PAGE_SIZE)} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-blue-600 disabled:opacity-50 dark:border-gray-700 dark:text-blue-300">
      {t(`${label}.loadMore`)}
    </button>}
  </section>;
}
