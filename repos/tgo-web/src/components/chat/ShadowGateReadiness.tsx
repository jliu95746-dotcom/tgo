import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { APIError } from '@/services/api';
import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import type { ShadowGateAssessment, ShadowGateSignoffReceipt } from '@/types/shadowReview';

/** The review gate and human signoff never change live customer reply routing. */
export default function ShadowGateReadiness({
  protocolId, revision, actorId,
}: { protocolId: string; revision: string; actorId: string }) {
  const { t } = useTranslation();
  const [assessment, setAssessment] = useState<ShadowGateAssessment | null>(null);
  const [signoff, setSignoff] = useState<ShadowGateSignoffReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [blindLocked, setBlindLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reference, setReference] = useState('');
  const [caseCount, setCaseCount] = useState(1);
  const [requestNote, setRequestNote] = useState('');
  const [decisionNote, setDecisionNote] = useState('');
  const [liveVerified, setLiveVerified] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setAssessment(null);
    setSignoff(null);
    setLoading(true);
    setFailed(false);
    setBlindLocked(false);
    void Promise.all([
      messageAnalysisApiService.getShadowGateReadiness(protocolId, controller.signal),
      messageAnalysisApiService.getShadowGateSignoff(protocolId, controller.signal),
    ])
      .then(([result, record]) => {
        if (!controller.signal.aborted) {
          setAssessment(result);
          setSignoff(record);
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        if (error instanceof APIError && error.getErrorCode() === 'SHADOW_BLIND_REVIEW_LOCKED') {
          setBlindLocked(true);
        } else {
          setFailed(true);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [protocolId, revision]);

  const reviewComplete = assessment?.status === 'review_complete'
    && assessment.can_request_g4_signoff && assessment.blockers.length === 0;

  async function requestSignoff() {
    if (!reviewComplete || busy || signoff) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await messageAnalysisApiService.requestShadowGateSignoff(protocolId, {
        request_id: crypto.randomUUID(),
        evidence_reference: reference.trim(),
        live_case_count: caseCount,
        note: requestNote.trim(),
      });
      setSignoff(result);
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('shadowReview.signoffActionFailed'));
      try {
        setSignoff(await messageAnalysisApiService.getShadowGateSignoff(protocolId));
      } catch { /* Preserve the original error and allow a page refresh. */ }
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision: 'accepted' | 'rejected') {
    if (!signoff || signoff.status !== 'pending' || signoff.requester_id === actorId || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await messageAnalysisApiService.decideShadowGateSignoff(protocolId, {
        request_id: crypto.randomUUID(), decision,
        note: decisionNote.trim(),
        live_evidence_verified: decision === 'accepted' && liveVerified,
      });
      setSignoff(result);
      setAssessment(await messageAnalysisApiService.getShadowGateReadiness(protocolId));
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('shadowReview.signoffActionFailed'));
      try {
        setSignoff(await messageAnalysisApiService.getShadowGateSignoff(protocolId));
      } catch { /* Preserve the original error and allow a page refresh. */ }
    } finally {
      setBusy(false);
    }
  }

  return <div className="mt-2 rounded border border-gray-200 p-2 dark:border-gray-700">
    <p className="font-medium">{t('shadowReview.gateTitle')}</p>
    {loading && <p>{t('shadowReview.gateLoading')}</p>}
    {failed && <p role="alert" className="text-red-600">{t('shadowReview.gateFailed')}</p>}
    {blindLocked && <p className="text-blue-600 dark:text-blue-400">
      {t('shadowReview.blindResultsLocked')}
    </p>}
    {assessment && <div role="status" className="mt-1 space-y-1">
      <p className={reviewComplete
        ? 'text-green-700 dark:text-green-400'
        : 'text-amber-700 dark:text-amber-400'}>
        {t(reviewComplete ? 'shadowReview.gateReviewComplete' : 'shadowReview.gateBlocked')}
      </p>
      {assessment.sample_count !== null && assessment.ready_count !== null && <p>
        {t('shadowReview.gateCounts', {
          ready: assessment.ready_count,
          total: assessment.sample_count,
          severe: assessment.historical_severe_count ?? 0,
        })}
      </p>}
      {assessment.blockers.length > 0 && <ul className="list-inside list-disc">
        {assessment.blockers.map(blocker => <li key={blocker}>
          {t(`shadowReview.gateBlockers.${blocker}`)}
        </li>)}
      </ul>}
      <p className="text-gray-500 dark:text-gray-400">{t('shadowReview.gateLiveCaveat')}</p>
      {!signoff && reviewComplete && <div className="space-y-2 border-t border-gray-200 pt-2 dark:border-gray-700">
        <p className="font-medium">{t('shadowReview.signoffRequestTitle')}</p>
        <p className="text-gray-500 dark:text-gray-400">{t('shadowReview.signoffPrivacyHint')}</p>
        <label className="block">{t('shadowReview.signoffEvidenceReference')}
          <input value={reference} onChange={event => setReference(event.target.value)}
            maxLength={120} placeholder={t('shadowReview.signoffEvidenceExample')}
            className="mt-1 w-full rounded border border-gray-300 bg-transparent p-1 dark:border-gray-600" />
        </label>
        <label className="block">{t('shadowReview.signoffLiveCases')}
          <input type="number" min={1} max={100000} value={caseCount}
            onChange={event => setCaseCount(Number(event.target.value))}
            className="mt-1 w-full rounded border border-gray-300 bg-transparent p-1 dark:border-gray-600" />
        </label>
        <label className="block">{t('shadowReview.signoffRequestNote')}
          <textarea value={requestNote} onChange={event => setRequestNote(event.target.value)}
            maxLength={500} rows={2}
            className="mt-1 w-full rounded border border-gray-300 bg-transparent p-1 dark:border-gray-600" />
        </label>
        <button type="button" disabled={busy || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{5,119}$/.test(reference.trim())
          || !Number.isInteger(caseCount) || caseCount < 1 || requestNote.trim().length < 10}
          onClick={() => void requestSignoff()}
          className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
          {t('shadowReview.signoffRequestButton')}
        </button>
      </div>}
      {signoff && <div className="space-y-2 border-t border-gray-200 pt-2 dark:border-gray-700">
        <p className="font-medium">{t(`shadowReview.signoffStatuses.${signoff.status}`)}</p>
        <p>{t('shadowReview.signoffRecord', {
          reference: signoff.evidence_reference, count: signoff.live_case_count,
          date: new Date(signoff.created_at).toLocaleString(),
        })}</p>
        <p className="break-all text-gray-500 dark:text-gray-400">
          {t('shadowReview.signoffRequester')}: {signoff.requester_id}
        </p>
        <p className="break-all text-gray-500 dark:text-gray-400">
          {t('shadowReview.signoffManifest')}: {signoff.manifest_sha256}
        </p>
        <p className="break-all text-gray-500 dark:text-gray-400">
          {t('shadowReview.signoffDigest')}: {signoff.review_evidence_sha256}
        </p>
        <p>{signoff.note}</p>
        {signoff.decider_id && signoff.decided_at && <p className="break-all">
          {t('shadowReview.signoffDecider')}: {signoff.decider_id} · {new Date(signoff.decided_at).toLocaleString()}
        </p>}
        {signoff.decision_note && <p>{t('shadowReview.signoffDecisionNote')}: {signoff.decision_note}</p>}
        {signoff.status === 'accepted' && !reviewComplete && <p className="text-red-600">
          {t('shadowReview.signoffNowBlocked')}
        </p>}
        {signoff.status === 'pending' && signoff.requester_id === actorId &&
          <p>{t('shadowReview.signoffOtherAdmin')}</p>}
        {signoff.status === 'pending' && signoff.requester_id !== actorId && <div className="space-y-2">
          <label className="block">{t('shadowReview.signoffDecisionNote')}
            <textarea value={decisionNote} onChange={event => setDecisionNote(event.target.value)}
              maxLength={500} rows={2}
              className="mt-1 w-full rounded border border-gray-300 bg-transparent p-1 dark:border-gray-600" />
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={liveVerified}
              onChange={event => setLiveVerified(event.target.checked)} />
            {t('shadowReview.signoffLiveVerified')}
          </label>
          <div className="flex gap-2">
            <button type="button" disabled={busy || !liveVerified || decisionNote.trim().length < 5 || !reviewComplete}
              onClick={() => void decide('accepted')}
              className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
              {t('shadowReview.signoffAccept')}
            </button>
            <button type="button" disabled={busy || decisionNote.trim().length < 5}
              onClick={() => void decide('rejected')}
              className="rounded border border-gray-300 px-2 py-1 disabled:opacity-50 dark:border-gray-600">
              {t('shadowReview.signoffReject')}
            </button>
          </div>
        </div>}
      </div>}
      {actionError && <p role="alert" className="text-red-600">
        {actionError}
      </p>}
    </div>}
  </div>;
}
