import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import type { ShadowPendingReviewQueue as PendingQueue } from '@/types/shadowReview';

/** Admin-only links to verified, unreviewed cases; no message text in this list. */
export default function ShadowReviewQueue({ revision }: { revision: number }) {
  const { t } = useTranslation();
  const [cursor, setCursor] = useState<string | null>(null);
  const [result, setResult] = useState<PendingQueue | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setFailed(false);
    setLoading(true);
    void messageAnalysisApiService.getShadowReviewQueue(cursor, controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult(data); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [cursor, revision]);

  return <div className="rounded-lg border border-gray-200 bg-white p-2 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
    <p className="font-medium">{t('shadowReview.queueTitle')}</p>
    {result && <p>{t('shadowReview.queueCount', { count: result.total_pending })}</p>}
    {loading && <p>{t('shadowReview.loading')}</p>}
    {failed && <p role="alert" className="text-red-600">{t('shadowReview.queueFailed')}</p>}
    {result?.items.length === 0 && <p>{t('shadowReview.queueEmpty')}</p>}
    {result && result.items.length > 0 && <ul className="mt-1 space-y-1">
      {result.items.map((item, index) => <li key={item.draft_id}>
        <Link className="text-blue-600 hover:underline dark:text-blue-400"
          to={`/chat/251/${item.visitor_id}-vtr?shadowReview=1&shadowDraftId=${item.draft_id}`}>
          {t('shadowReview.queueCase', {
            number: index + 1,
            time: new Date(item.created_at).toLocaleString(),
          })}
        </Link>
      </li>)}
    </ul>}
    <div className="mt-1 flex gap-3">
      {cursor && <button type="button" disabled={loading} onClick={() => setCursor(null)}
        className="text-blue-600 disabled:opacity-50 dark:text-blue-400">
        {t('shadowReview.queueFirst')}
      </button>}
      {result?.next_cursor && <button type="button" disabled={loading}
        onClick={() => setCursor(result.next_cursor)}
        className="text-blue-600 disabled:opacity-50 dark:text-blue-400">
        {t('shadowReview.queueNext')}
      </button>}
    </div>
    <p className="mt-1">{t('shadowReview.queueCaveat')}</p>
  </div>;
}
