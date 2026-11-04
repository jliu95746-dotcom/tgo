import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { WorkflowApiService } from '@/services/workflowApi';
import type {
  WorkflowAfterSalesHandoffSummary, WorkflowAfterSalesReviewSummary,
} from '@/types/workflow';

interface Props {
  review: WorkflowAfterSalesReviewSummary;
}

export default function AfterSalesHandoffPanel({ review }: Props) {
  const { t } = useTranslation();
  const copy = 'businessScenarios.workflow.approvals.handoff';
  const [receipt, setReceipt] = useState<WorkflowAfterSalesHandoffSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setReceipt(null);
    setError('');
    WorkflowApiService.getAftersalesHandoff(review.id)
      .then(value => {
        if (!cancelled) { setReceipt(value); setUncertain(false); }
      })
      .catch(() => {
        if (!cancelled) setError(t(`${copy}.loadError`));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [review.id, reload, t]);

  const requestHandoff = async () => {
    if (busy || uncertain) return;
    setBusy(true);
    setError('');
    try {
      setReceipt(await WorkflowApiService.requestAftersalesHandoff(review.id));
    } catch {
      setUncertain(true);
      setError(t(`${copy}.requestError`));
    } finally {
      setBusy(false);
    }
  };

  const statusKey = receipt?.status === 'active' ? 'active'
    : receipt?.status === 'waiting' || receipt?.status === 'queued' ? 'waiting'
      : 'other';
  return <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/20">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t(`${copy}.title`)}</p>
      <button type="button" disabled={loading || busy} onClick={() => setReload(value => value + 1)}
        className="rounded-lg border border-blue-300 px-3 py-1 text-xs text-blue-700 disabled:opacity-50 dark:border-blue-700 dark:text-blue-300">
        {t(`${copy}.refresh`)}
      </button>
    </div>
    <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.scope`)}</p>
    <p className="mt-2 break-all text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.session`)} {review.visitor_session_id}</p>
    {loading && <p className="mt-3 text-sm text-gray-500">{t(`${copy}.loading`)}</p>}
    {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}
    {!loading && receipt && <>
      <p role="status" className="mt-3 text-sm font-semibold text-blue-700 dark:text-blue-300">{t(`${copy}.recorded`)}</p>
      <p className="mt-1 text-sm text-gray-700 dark:text-gray-200">{t(`${copy}.status.${statusKey}`)}</p>
      <p className="mt-1 text-sm text-gray-700 dark:text-gray-200">{t(`${copy}.notification.${receipt.notification_status}`)}</p>
      <p className="mt-2 break-all text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.requestId`)} {receipt.request_id}</p>
    </>}
    {!loading && !receipt && !error && !uncertain && <>
      <p className="mt-3 text-sm text-gray-700 dark:text-gray-200">{t(`${copy}.confirmHint`)}</p>
      <button type="button" disabled={busy} onClick={() => void requestHandoff()}
        className="mt-3 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">
        {t(`${copy}.confirm`)}
      </button>
    </>}
  </div>;
}
