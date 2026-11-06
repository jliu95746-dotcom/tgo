import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import AfterSalesIntakePanel from './AfterSalesIntakePanel';
import AfterSalesHandoffPanel from './AfterSalesHandoffPanel';
import WorkflowReconciliations from './WorkflowReconciliations';
import type {
  WorkflowAfterSalesEvidenceFile, WorkflowAfterSalesReviewResult,
  WorkflowAfterSalesReviewSummary,
  WorkflowApprovalSummary,
} from '@/types/workflow';

type Decision = 'approve' | 'reject';

export default function WorkflowApprovals() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const staffId = useAuthStore(state => state.user?.id);
  const projectId = useAuthStore(state => state.user?.project_id);
  const [items, setItems] = useState<WorkflowApprovalSummary[]>([]);
  const [reviews, setReviews] = useState<WorkflowAfterSalesReviewSummary[]>([]);
  const [reviewCursor, setReviewCursor] = useState<WorkflowAfterSalesReviewSummary | null>(null);
  const [reviewHasMore, setReviewHasMore] = useState(false);
  const reviewListRequest = useRef(0);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState(false);
  const [reviewDecisionError, setReviewDecisionError] = useState('');
  const [reviewMessage, setReviewMessage] = useState('');
  const [fileReviewId, setFileReviewId] = useState<string | null>(null);
  const [intakeReviewId, setIntakeReviewId] = useState<string | null>(null);
  const [handoffReviewId, setHandoffReviewId] = useState<string | null>(null);
  const [reviewFiles, setReviewFiles] = useState<WorkflowAfterSalesEvidenceFile[]>([]);
  const [reviewFilesLoading, setReviewFilesLoading] = useState(false);
  const [reviewFilesError, setReviewFilesError] = useState(false);
  const [chatTargetBusyId, setChatTargetBusyId] = useState<string | null>(null);
  const [chatTargetError, setChatTargetError] = useState('');
  const fileRequest = useRef(0);
  const [busyReviewId, setBusyReviewId] = useState<string | null>(null);
  const [selectedReview, setSelectedReview] = useState<{
    id: string; result: WorkflowAfterSalesReviewResult | '';
  } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ id: string; decision: Decision } | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setItems([]);
    setSelected(null);
    if (!staffId || !projectId) return () => { cancelled = true; };
    setLoading(true);
    setError('');
    WorkflowApiService.listApprovals()
      .then(rows => { if (!cancelled) setItems(rows); })
      .catch(() => { if (!cancelled) setError(t('businessScenarios.workflow.approvals.loadFailed')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [staffId, projectId, refresh, t]);

  useEffect(() => {
    let cancelled = false;
    reviewListRequest.current += 1;
    setReviews([]);
    setReviewCursor(null);
    setReviewHasMore(false);
    setSelectedReview(null);
    setReviewError(false);
    fileRequest.current += 1;
    setFileReviewId(null);
    setIntakeReviewId(null);
    setHandoffReviewId(null);
    setReviewFiles([]);
    setReviewFilesLoading(false);
    setReviewFilesError(false);
    setChatTargetError('');
    if (!staffId || !projectId) return () => { cancelled = true; };
    setReviewLoading(true);
    WorkflowApiService.listAftersalesReviews()
      .then(rows => {
        if (cancelled) return;
        setReviews(rows);
        setReviewCursor(rows.length > 0 ? rows[rows.length - 1] : null);
        setReviewHasMore(rows.length === 30);
      })
      .catch(() => { if (!cancelled) setReviewError(true); })
      .finally(() => { if (!cancelled) setReviewLoading(false); });
    return () => { cancelled = true; };
  }, [staffId, projectId, refresh]);

  const loadMoreReviews = async () => {
    if (!reviewCursor || !reviewHasMore || reviewLoading || !staffId || !projectId) return;
    const request = reviewListRequest.current;
    setReviewLoading(true);
    setReviewError(false);
    try {
      const rows = await WorkflowApiService.listAftersalesReviews(30, reviewCursor);
      if (reviewListRequest.current !== request) return;
      setReviews(current => {
        const known = new Set(current.map(row => row.id));
        return [...current, ...rows.filter(row => !known.has(row.id))];
      });
      setReviewCursor(rows.length > 0 ? rows[rows.length - 1] : null);
      setReviewHasMore(rows.length === 30);
    } catch {
      if (reviewListRequest.current === request) setReviewError(true);
    } finally {
      if (reviewListRequest.current === request) setReviewLoading(false);
    }
  };

  const decide = async (item: WorkflowApprovalSummary, decision: Decision) => {
    if (busyId) return;
    if (!/^[0-9a-f]{64}$/.test(item.preview_digest)) {
      setError(t('businessScenarios.workflow.approvals.previewUnavailable'));
      return;
    }
    setBusyId(item.id);
    setMessage('');
    setError('');
    try {
      const result = await WorkflowApiService.decideApproval(
        item.id, decision, item.preview_digest,
      );
      setSelected(null);
      setMessage(t(`businessScenarios.workflow.approvals.${result.dispatch_status === 'unknown' ? 'dispatchUnknown' : decision === 'reject' ? 'rejected' : 'approved'}`));
      setRefresh(value => value + 1);
    } catch {
      setError(t('businessScenarios.workflow.approvals.decisionFailed'));
    } finally {
      setBusyId(null);
    }
  };

  const decideReview = async (review: WorkflowAfterSalesReviewSummary) => {
    if (!selectedReview?.result || busyReviewId) return;
    if (!/^[0-9a-f]{64}$/.test(review.review_digest)) {
      setReviewDecisionError(t('businessScenarios.workflow.approvals.previewUnavailable'));
      return;
    }
    setBusyReviewId(review.id);
    setReviewDecisionError('');
    setReviewMessage('');
    try {
      const decided = await WorkflowApiService.decideAftersalesReview(
        review.id, selectedReview.result, review.review_digest,
      );
      setReviews(rows => rows.map(row => row.id === review.id ? decided : row));
      setSelectedReview(null);
      setReviewMessage(t('businessScenarios.workflow.approvals.reviewSaved'));
    } catch {
      setReviewDecisionError(t('businessScenarios.workflow.approvals.reviewDecisionFailed'));
    } finally {
      setBusyReviewId(null);
    }
  };

  const toggleReviewFiles = async (reviewId: string) => {
    const request = ++fileRequest.current;
    if (fileReviewId === reviewId) {
      setFileReviewId(null);
      return;
    }
    setFileReviewId(reviewId);
    setReviewFiles([]);
    setReviewFilesError(false);
    setReviewFilesLoading(true);
    try {
      const files = await WorkflowApiService.listAftersalesReviewFiles(reviewId);
      if (fileRequest.current === request) setReviewFiles(files);
    } catch {
      if (fileRequest.current === request) setReviewFilesError(true);
    } finally {
      if (fileRequest.current === request) setReviewFilesLoading(false);
    }
  };

  const openCustomerChat = async (id: string, source: 'approval' | 'review') => {
    if (chatTargetBusyId) return;
    setChatTargetBusyId(id);
    setChatTargetError('');
    try {
      const target = source === 'approval'
        ? await WorkflowApiService.getApprovalChatTarget(id)
        : await WorkflowApiService.getAftersalesChatTarget(id);
      if (target.channel_type !== 251 || !/^[0-9a-f-]{36}-vtr$/i.test(target.channel_id)) {
        throw new Error('Invalid aftersales chat target');
      }
      const messageSeq = target.message_seq;
      const query = typeof messageSeq === 'number'
        && Number.isSafeInteger(messageSeq) && messageSeq > 0
        ? `?message_seq=${messageSeq}` : '';
      navigate(`/chat/${target.channel_type}/${encodeURIComponent(target.channel_id)}${query}`);
    } catch {
      setChatTargetError(t('businessScenarios.workflow.approvals.chatTargetError'));
    } finally {
      setChatTargetBusyId(null);
    }
  };

  const copy = 'businessScenarios.workflow.approvals';
  return (
    <section className="space-y-4" aria-label={t(`${copy}.title`)}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t(`${copy}.title`)}</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t(`${copy}.scope`)}</p>
        </div>
        <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />{t(`${copy}.refresh`)}
        </button>
      </div>
      {message && <p role="status" className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">{message}</p>}
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}
      {chatTargetError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{chatTargetError}</p>}
      <WorkflowReconciliations refreshToken={refresh} />
      {!loading && items.length === 0 && !error &&
        <p className="rounded-2xl border border-gray-200 bg-white px-5 py-8 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">{t(`${copy}.empty`)}</p>}
      {items.map(item => (
        <article key={item.id} className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="whitespace-pre-wrap break-words text-sm font-semibold text-gray-900 dark:text-gray-100">{item.summary}</p>
              <p className="mt-2 break-all text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.execution`)} {item.execution_id}</p>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {item.status === 'pending'
                  ? `${t(`${copy}.expires`)} ${new Date(item.expires_at).toLocaleString()}`
                  : t(`${copy}.pendingResume`)}
              </p>
              {item.customer_session_available && <button type="button"
                disabled={chatTargetBusyId !== null}
                onClick={() => void openCustomerChat(item.id, 'approval')}
                className="mt-3 rounded-lg border border-blue-300 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-950/30">
                {t(`${copy}.openApprovalChat`)}
              </button>}
            </div>
            {item.status === 'approved'
              ? <button type="button" disabled={busyId !== null} onClick={() => void decide(item, 'approve')}
                  className="rounded-xl border border-purple-300 px-4 py-2 text-sm font-semibold text-purple-700 hover:bg-purple-50 disabled:opacity-50 dark:border-purple-700 dark:text-purple-300 dark:hover:bg-purple-950/30">
                  {t(`${copy}.retryDispatch`)}
                </button>
              : <div className="flex gap-2">
                  <button type="button" disabled={busyId !== null} onClick={() => setSelected({ id: item.id, decision: 'reject' })}
                    className="rounded-xl border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800">{t(`${copy}.reject`)}</button>
                  <button type="button" disabled={busyId !== null} onClick={() => setSelected({ id: item.id, decision: 'approve' })}
                    className="rounded-xl bg-purple-600 px-4 py-2 text-sm font-semibold text-white hover:bg-purple-700 disabled:opacity-50">{t(`${copy}.approve`)}</button>
                </div>}
          </div>
          {selected?.id === item.id && <div className="mt-4 rounded-xl border border-purple-200 bg-purple-50 p-4 dark:border-purple-800 dark:bg-purple-950/20">
            <p className="text-sm text-gray-800 dark:text-gray-200">{t(`${copy}.${selected.decision === 'approve' ? 'confirmApprove' : 'confirmReject'}`)}</p>
            <p className="mt-3 text-xs font-semibold text-gray-600 dark:text-gray-300">{t(`${copy}.previewLabel`)}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-800 dark:text-gray-200">{item.summary}</p>
            <div className="mt-3 flex gap-2">
              <button type="button" disabled={busyId !== null} onClick={() => setSelected(null)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700">{t(`${copy}.cancel`)}</button>
              <button type="button" disabled={busyId !== null} onClick={() => void decide(item, selected.decision)} className="rounded-lg bg-purple-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">{t(`${copy}.confirm`)}</button>
            </div>
          </div>}
        </article>
      ))}
      <div className="border-t border-gray-200 pt-5 dark:border-gray-800">
        <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t(`${copy}.reviewTitle`)}</h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t(`${copy}.reviewHint`)}</p>
      </div>
      {reviewError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{t(`${copy}.reviewLoadFailed`)}</p>}
      {reviewMessage && <p role="status" className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">{reviewMessage}</p>}
      {reviewDecisionError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{reviewDecisionError}</p>}
      {!reviewLoading && !reviewError && reviews.length === 0 &&
        <p className="rounded-2xl border border-gray-200 bg-white px-5 py-8 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">{t(`${copy}.reviewEmpty`)}</p>}
      {reviews.map(review => <article key={review.id}
        className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t(`businessScenarios.workflow.approvalConfig.categories.${review.category}`)}</p>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${review.status === 'reviewed' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300' : 'bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300'}`}>
            {t(`${copy}.${review.status === 'reviewed' ? 'reviewedStatus' : 'reviewStatus'}`)}
          </span>
        </div>
        <p className="mt-3 break-all text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.reviewSession`)} {review.visitor_session_id}</p>
        <p className="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.execution`)} {review.execution_id}</p>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.reviewCreated`)} {new Date(review.created_at).toLocaleString()}</p>
        <button type="button" disabled={chatTargetBusyId !== null} onClick={() => void openCustomerChat(review.id, 'review')}
          className="mt-3 mr-2 rounded-lg border border-blue-300 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-950/30">
          {t(`${copy}.openReviewChat`)}
        </button>
        <button type="button" onClick={() => void toggleReviewFiles(review.id)}
          className="mt-3 rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800">
          {t(`${copy}.${fileReviewId === review.id ? 'hideReviewFiles' : 'showReviewFiles'}`)}
        </button>
        {fileReviewId === review.id && <div className="mt-3 rounded-xl border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800/50">
          <p className="text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.reviewFilesHint`)}</p>
          {reviewFilesLoading && <p className="mt-2 text-xs text-gray-500">{t(`${copy}.reviewFilesLoading`)}</p>}
          {reviewFilesError && <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-300">{t(`${copy}.reviewFilesError`)}</p>}
          {!reviewFilesLoading && !reviewFilesError && reviewFiles.length === 0 &&
            <p className="mt-2 text-xs text-gray-500">{t(`${copy}.reviewFilesEmpty`)}</p>}
          {reviewFiles.length > 0 && <ul className="mt-2 space-y-1 text-xs text-gray-700 dark:text-gray-200">
            {reviewFiles.map(file => <li key={file.id} className="break-words">
              {file.file_name} · {new Date(file.created_at).toLocaleString()}
            </li>)}
          </ul>}
        </div>}
        {review.status === 'reviewed' && review.review_result &&
          <p className="mt-3 text-sm text-gray-700 dark:text-gray-300">
            {t(`${copy}.reviewResultLabel`)} {t(`${copy}.reviewResults.${review.review_result}`)}
            {review.reviewed_at ? ` · ${new Date(review.reviewed_at).toLocaleString()}` : ''}
          </p>}
        {review.status === 'reviewed' && review.review_result === 'needs_manual_service' && <>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setIntakeReviewId(current => current === review.id ? null : review.id)}
              className="rounded-lg border border-purple-300 px-3 py-1.5 text-xs font-semibold text-purple-700 hover:bg-purple-50 dark:border-purple-700 dark:text-purple-300 dark:hover:bg-purple-950/30">
              {t(`${copy}.intake.${intakeReviewId === review.id ? 'hide' : 'show'}`)}
            </button>
            <button type="button" onClick={() => setHandoffReviewId(current => current === review.id ? null : review.id)}
              className="rounded-lg border border-blue-300 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-950/30">
              {t(`${copy}.handoff.${handoffReviewId === review.id ? 'hide' : 'show'}`)}
            </button>
          </div>
          {intakeReviewId === review.id && <AfterSalesIntakePanel review={review} />}
          {handoffReviewId === review.id && <AfterSalesHandoffPanel review={review} />}
        </>}
        {review.status === 'pending_staff_review' && <button type="button"
          disabled={busyReviewId !== null}
          onClick={() => setSelectedReview({ id: review.id, result: '' })}
          className="mt-4 rounded-xl border border-purple-300 px-4 py-2 text-sm font-semibold text-purple-700 hover:bg-purple-50 disabled:opacity-50 dark:border-purple-700 dark:text-purple-300 dark:hover:bg-purple-950/30">
          {t(`${copy}.recordReview`)}
        </button>}
        {selectedReview?.id === review.id && <div className="mt-4 rounded-xl border border-purple-200 bg-purple-50 p-4 dark:border-purple-800 dark:bg-purple-950/20">
          <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">{t(`${copy}.confirmReview`)}</p>
          <p className="mt-2 break-all text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.reviewSession`)} {review.visitor_session_id}</p>
          <label htmlFor={`review-result-${review.id}`} className="mt-3 block text-xs font-semibold text-gray-600 dark:text-gray-300">{t(`${copy}.reviewResultLabel`)}</label>
          <select id={`review-result-${review.id}`} value={selectedReview.result}
            onChange={event => setSelectedReview({ id: review.id, result: event.target.value as WorkflowAfterSalesReviewResult | '' })}
            className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
            <option value="">{t(`${copy}.chooseReviewResult`)}</option>
            <option value="needs_manual_service">{t(`${copy}.reviewResults.needs_manual_service`)}</option>
            <option value="needs_customer_information">{t(`${copy}.reviewResults.needs_customer_information`)}</option>
            <option value="not_aftersales">{t(`${copy}.reviewResults.not_aftersales`)}</option>
          </select>
          <p className="mt-2 text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.reviewDecisionHint`)}</p>
          <div className="mt-3 flex gap-2">
            <button type="button" onClick={() => setSelectedReview(null)} disabled={busyReviewId !== null}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700">{t(`${copy}.cancel`)}</button>
            <button type="button" onClick={() => void decideReview(review)} disabled={!selectedReview.result || busyReviewId !== null}
              className="rounded-lg bg-purple-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">{t(`${copy}.confirm`)}</button>
          </div>
        </div>}
      </article>)}
      {reviewHasMore && <button type="button" onClick={() => void loadMoreReviews()}
        disabled={reviewLoading}
        className="rounded-xl border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800">
        {t(`${copy}.reviewMore`)}
      </button>}
    </section>
  );
}
