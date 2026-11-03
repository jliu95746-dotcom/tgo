import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import type { ShadowAdjudicationReceipt, ShadowSampleItem } from '@/types/shadowReview';

interface Props {
  batchId: string;
  item: ShadowSampleItem;
}

/** Read the immutable audit trail only when an administrator expands it. */
export default function ShadowAdjudicationHistory({ batchId, item }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ShadowAdjudicationReceipt[]>([]);
  const [requestCursor, setRequestCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [retry, setRetry] = useState(0);
  const currentHash = item.review_set_sha256;
  const currentVerdict = item.adjudication_verdict;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    void messageAnalysisApiService.getShadowAdjudicationHistory(
      batchId, item.draft_id, item.visitor_id, requestCursor, controller.signal,
    ).then(page => {
      if (controller.signal.aborted) return;
      setItems(previous => requestCursor ? [...previous, ...page.items] : page.items);
      setHasMore(page.has_more);
      setNextCursor(page.next_cursor);
      setLoaded(true);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [open, batchId, item.draft_id, item.visitor_id, currentHash, currentVerdict, requestCursor, retry]);

  return <details className="mt-2 text-xs" onToggle={event => {
    const expanded = event.currentTarget.open;
    setOpen(expanded);
    if (!expanded) {
      setItems([]);
      setRequestCursor(null);
      setNextCursor(null);
      setHasMore(false);
      setLoaded(false);
    }
  }}>
    <summary className="cursor-pointer text-blue-600 dark:text-blue-400">
      {t('shadowReview.adjudicationHistoryTitle')}
    </summary>
    {items.length === 0 && loaded && !loading && !failed &&
      <p className="mt-1 text-gray-500">{t('shadowReview.adjudicationHistoryNone')}</p>}
    {items.length > 0 && <ol className="mt-1 space-y-2">
      {items.map((decision, index) => {
        const isCurrent = index === 0 && currentHash === decision.review_set_sha256
          && currentVerdict === decision.verdict;
        return <li key={decision.id} className="rounded border border-gray-200 p-2 dark:border-gray-700">
          <p className="font-medium text-gray-700 dark:text-gray-200">
            {t(`shadowReview.verdicts.${decision.verdict}`)} · {t(isCurrent
              ? 'shadowReview.adjudicationHistoryCurrent'
              : 'shadowReview.adjudicationHistoryOutdated')}
          </p>
          <p className="text-gray-500">
            {new Date(decision.created_at).toLocaleString()} · {t('shadowReview.adjudicationHistoryBy')}: {decision.adjudicator_id}
          </p>
          {decision.reason_code && <p className="text-gray-500">{t(`shadowReview.reasons.${decision.reason_code}`)}</p>}
          <p className="whitespace-pre-wrap break-words text-gray-700 dark:text-gray-200">{decision.note}</p>
        </li>;
      })}
    </ol>}
    {loading && <p className="text-gray-500">{t('shadowReview.loading')}</p>}
    {failed && <button type="button" className="text-red-600" onClick={() => setRetry(value => value + 1)}>
      {t('shadowReview.adjudicationHistoryFailed')}
    </button>}
    {hasMore && nextCursor && !loading && !failed && <button type="button"
      className="mt-1 text-blue-600 dark:text-blue-400"
      onClick={() => setRequestCursor(nextCursor)}>
      {t('shadowReview.adjudicationHistoryMore')}
    </button>}
  </details>;
}
