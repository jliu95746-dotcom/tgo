import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router-dom';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import { APIError } from '@/services/api';
import { useAuthStore } from '@/stores/authStore';
import type { ShadowComparisonsResponse, ShadowReviewSummary } from '@/types/shadowReview';
import ShadowReviewForm from './ShadowReviewForm';
import ShadowReviewQueue from './ShadowReviewQueue';
import ShadowSampleBatches from './ShadowSampleBatches';

interface ShadowReviewPanelProps {
  visitorId: string;
}

/** Staff comparison and admin assessment; neither action sends a customer reply. */
export default function ShadowReviewPanel({ visitorId }: ShadowReviewPanelProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const focusDraftId = new URLSearchParams(location.search).get('shadowDraftId');
  const staffId = useAuthStore(state => state.user?.id);
  const isAdmin = useAuthStore(state => state.user?.role === 'admin');
  const [open, setOpen] = useState(
    () => new URLSearchParams(location.search).get('shadowReview') === '1',
  );
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [result, setResult] = useState<ShadowComparisonsResponse | null>(null);
  const [summary, setSummary] = useState<ShadowReviewSummary | null>(null);
  const [summaryFailed, setSummaryFailed] = useState(false);
  const [summaryLocked, setSummaryLocked] = useState(false);

  useEffect(() => {
    if (!open || !staffId) return;
    const controller = new AbortController();
    setResult(null);
    setSummary(null);
    setSummaryFailed(false);
    setSummaryLocked(false);
    setFailed(false);
    setLoading(true);
    void messageAnalysisApiService.getShadowComparisons(visitorId, controller.signal, focusDraftId)
      .then(data => { if (!controller.signal.aborted) setResult(data); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    if (isAdmin) {
      void messageAnalysisApiService.getShadowReviewSummary(controller.signal)
        .then(data => { if (!controller.signal.aborted) setSummary(data); })
        .catch(error => {
          if (controller.signal.aborted) return;
          if (error instanceof APIError && error.getErrorCode() === 'SHADOW_BLIND_REVIEW_LOCKED') {
            setSummaryLocked(true);
          } else {
            setSummaryFailed(true);
          }
        });
    }
    return () => controller.abort();
  }, [open, revision, staffId, visitorId, isAdmin, focusDraftId]);

  return (
    <section className="border-t border-gray-200 bg-gray-50 px-4 py-2 text-sm dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-center justify-between gap-2">
        <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}
          className="inline-flex items-center gap-1 font-medium text-gray-700 dark:text-gray-200">
          {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          {t('shadowReview.title')}
        </button>
        {open && <button type="button" onClick={() => setRevision(value => value + 1)}
          disabled={loading} className="inline-flex items-center gap-1 text-blue-600 disabled:opacity-50 dark:text-blue-400">
          <RefreshCw size={14} />{t('shadowReview.refresh')}
        </button>}
      </div>
      {open && <div className="mt-2 max-h-64 space-y-2 overflow-y-auto" role="region" aria-label={t('shadowReview.title')}>
        {focusDraftId && <p className="text-xs text-gray-500">
          {t('shadowReview.focusedCase')}{' · '}
          <Link className="text-blue-600 hover:underline dark:text-blue-400"
            to={`/chat/251/${visitorId}-vtr?shadowReview=1`}>
            {t('shadowReview.showRecent')}
          </Link>
        </p>}
        {loading && <p className="text-gray-500">{t('shadowReview.loading')}</p>}
        {failed && <p role="alert" className="text-red-600">{t('shadowReview.failed')}</p>}
        {isAdmin && summary && <div className="rounded-lg border border-gray-200 bg-white p-2 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
          <p className="font-medium">{t('shadowReview.summaryTitle')}</p>
          <p>{t('shadowReview.summaryCounts', {
            eligible: summary.eligible_drafts, evaluated: summary.evaluated_drafts,
            unreviewed: summary.unreviewed_drafts, insufficient: summary.insufficient_drafts,
          })}</p>
          <p>{t('shadowReview.severeCounts', {
            ever: summary.ever_severe_drafts, current: summary.current_severe_drafts,
          })}</p>
          <p>{t('shadowReview.verifiedCounts', {
            eligible: summary.verified_eligible_drafts,
            evaluated: summary.verified_evaluated_drafts,
            severe: summary.verified_ever_severe_drafts,
          })}</p>
          <p>{t('shadowReview.summaryCaveat')}</p>
        </div>}
        {isAdmin && summaryFailed && <p role="alert" className="text-xs text-red-600">{t('shadowReview.summaryFailed')}</p>}
        {isAdmin && summaryLocked && <p className="text-xs text-blue-600 dark:text-blue-400">{t('shadowReview.blindResultsLocked')}</p>}
        {isAdmin && <ShadowReviewQueue revision={revision} />}
        {isAdmin && <ShadowSampleBatches revision={revision} />}
        {isAdmin && <Link className="block text-blue-600 hover:underline dark:text-blue-400" to="/shadow-acceptance">
          {t('hiddenShadow.openWorkspace')}
        </Link>}
        {result && result.items.length === 0 && <p className="text-gray-500">{t('shadowReview.empty')}</p>}
        {result?.items.map(item => (
          <article key={item.source_message_id} className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-gray-800 dark:text-gray-100">
              <strong>{t(`chat.analysis.intent.${item.intent}`, { defaultValue: item.intent })}</strong>
              <span>{Math.round(item.confidence * 100)}%</span>
              <time className="text-xs text-gray-500" dateTime={item.created_at}>
                {new Date(item.created_at).toLocaleString()}
              </time>
            </div>
            {item.replies.length === 0 && <p className="mt-2 text-gray-500">{t('shadowReview.noReply')}</p>}
            {item.replies.map(reply => (
              <div key={reply.client_msg_no} className="mt-2 border-l-2 border-blue-500 pl-2">
                {reply.ai_draft && <details className="mb-2 text-gray-600 dark:text-gray-300">
                  <summary className="cursor-pointer text-xs">{t('shadowReview.aiDraft')}</summary>
                  <p className="mt-1 whitespace-pre-wrap break-words">{reply.ai_draft}</p>
                </details>}
                {reply.ai_draft_id && <p className="mb-1 text-xs text-gray-500">
                  {t(reply.source_verified ? 'shadowReview.sourceVerified' : 'shadowReview.sourceUnverified')}
                </p>}
                {reply.ai_draft_id && <details className="mb-2 text-xs text-gray-500 dark:text-gray-400">
                  <summary className="cursor-pointer">{t('shadowReview.generationTrace')}</summary>
                  {!reply.generation_trace?.length
                    ? <p className="mt-1">{t('shadowReview.traceUnavailable')}</p>
                    : <ul className="mt-1 space-y-1">
                      {(reply.generation_trace ?? []).map((trace, index) => <li key={`${trace.phase}-${index}`}>
                        <span>{t(`shadowReview.tracePhase.${trace.phase}`)} · {t('shadowReview.configuredModel')}: {trace.model_name || t('shadowReview.traceUnavailable')}</span>
                        <span className="block break-all">{t('shadowReview.promptFingerprint')}: <code>{trace.configured_prompt_sha256 || t('shadowReview.traceUnavailable')}</code></span>
                        {trace.api_template_sha256 && <span className="block break-all">{t('shadowReview.apiTemplateFingerprint')}: <code>{trace.api_template_sha256}</code></span>}
                        {trace.requested_agent_id && <span className="block break-all">{t('shadowReview.requestedAgent')}: <code>{trace.requested_agent_id}</code></span>}
                        {trace.request_system_prompt_sha256 && <span className="block break-all">{t('shadowReview.requestPromptFingerprint')}: <code>{trace.request_system_prompt_sha256}</code></span>}
                      </li>)}
                    </ul>}
                  <p className="mt-1">{t('shadowReview.traceCaveat')}</p>
                </details>}
                <p className="text-xs text-gray-500">{t('shadowReview.staffReply')}</p>
                <p className="whitespace-pre-wrap break-words text-gray-700 dark:text-gray-200">{reply.content}</p>
                {reply.review && <p className="mt-1 text-xs text-gray-500">
                  {t('shadowReview.lastReview')}: {t(`shadowReview.verdicts.${reply.review.verdict}`)}
                  {reply.review.reason_code && ` · ${t(`shadowReview.reasons.${reply.review.reason_code}`)}`}
                </p>}
                {isAdmin && reply.ai_draft_id && <ShadowReviewForm
                  key={reply.ai_draft_id} draftId={reply.ai_draft_id} visitorId={visitorId}
                  onSaved={() => setRevision(value => value + 1)} />}
              </div>
            ))}
          </article>
        ))}
        {result?.has_more && <p className="text-xs text-amber-700 dark:text-amber-300">{t('shadowReview.more')}</p>}
        {result?.reply_limit_reached && <p className="text-xs text-amber-700 dark:text-amber-300">{t('shadowReview.replyLimit')}</p>}
        {result && <p className="text-xs text-gray-500">{t('shadowReview.reviewHint')}</p>}
      </div>}
    </section>
  );
}
