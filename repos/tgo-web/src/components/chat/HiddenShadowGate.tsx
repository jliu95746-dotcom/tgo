import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { APIError } from '@/services/api';
import { hiddenShadowApi } from '@/services/hiddenShadowApi';
import type {
  HiddenShadowGateAssessment, HiddenShadowGateSignoffReceipt,
} from '@/types/hiddenShadow';

interface Props {
  protocolId: string;
  actorId: string;
}

const referencePattern = /^[A-Za-z0-9][A-Za-z0-9._:/-]{5,119}$/;

/** Independent G4 signoff records evidence; it never changes customer reply routing. */
export default function HiddenShadowGate({ protocolId, actorId }: Props) {
  const { t } = useTranslation();
  const [assessment, setAssessment] = useState<HiddenShadowGateAssessment | null>(null);
  const [signoff, setSignoff] = useState<HiddenShadowGateSignoffReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reference, setReference] = useState('');
  const [liveCount, setLiveCount] = useState(0);
  const [requestNote, setRequestNote] = useState('');
  const [decisionNote, setDecisionNote] = useState('');
  const [liveVerified, setLiveVerified] = useState(false);
  const requestRetry = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const decisionRetry = useRef<{ fingerprint: string; requestId: string } | null>(null);

  const reload = useCallback(async (signal?: AbortSignal): Promise<void> => {
    setLoading(true);
    setLoadError(null);
    try {
      const [result, record] = await Promise.all([
        hiddenShadowApi.gateReadiness(protocolId, signal),
        hiddenShadowApi.signoff(protocolId, signal),
      ]);
      if (signal?.aborted) return;
      setAssessment(result);
      setSignoff(record);
    } catch (error) {
      if (signal?.aborted) return;
      setLoadError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.gateFailed'));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [protocolId, t]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const ready = Boolean(assessment?.can_request_g4_signoff
    && assessment.blockers.length === 0);
  const validRequest = ready && !signoff && referencePattern.test(reference.trim())
    && assessment?.sample_count === liveCount && requestNote.trim().length >= 10;

  async function requestSignoff(): Promise<void> {
    if (!validRequest || busy) return;
    const fingerprint = JSON.stringify([
      protocolId, reference.trim(), liveCount, requestNote.trim(),
    ]);
    if (requestRetry.current?.fingerprint !== fingerprint) {
      requestRetry.current = { fingerprint, requestId: crypto.randomUUID() };
    }
    setBusy(true);
    setActionError(null);
    try {
      const saved = await hiddenShadowApi.requestSignoff(protocolId, {
        request_id: requestRetry.current.requestId,
        evidence_reference: reference.trim(),
        live_case_count: liveCount,
        note: requestNote.trim(),
      });
      setSignoff(saved);
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.signoffActionFailed'));
      try { setSignoff(await hiddenShadowApi.signoff(protocolId)); }
      catch { /* Keep the error; retry uses the same request ID. */ }
    } finally { setBusy(false); }
  }

  async function decide(decision: 'accepted' | 'rejected'): Promise<void> {
    if (!signoff || signoff.status !== 'pending' || signoff.requester_id === actorId
      || decisionNote.trim().length < 5 || busy || (decision === 'accepted' && (!liveVerified || !ready))) return;
    const fingerprint = JSON.stringify([
      protocolId, decision, decisionNote.trim(), decision === 'accepted' && liveVerified,
    ]);
    if (decisionRetry.current?.fingerprint !== fingerprint) {
      decisionRetry.current = { fingerprint, requestId: crypto.randomUUID() };
    }
    setBusy(true);
    setActionError(null);
    try {
      const saved = await hiddenShadowApi.decideSignoff(protocolId, {
        request_id: decisionRetry.current.requestId,
        decision,
        note: decisionNote.trim(),
        live_evidence_verified: decision === 'accepted' && liveVerified,
      });
      setSignoff(saved);
      setAssessment(await hiddenShadowApi.gateReadiness(protocolId));
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.signoffActionFailed'));
      try { setSignoff(await hiddenShadowApi.signoff(protocolId)); }
      catch { /* Keep the error and allow a refresh. */ }
    } finally { setBusy(false); }
  }

  return <section className="mt-3 space-y-2 rounded border border-gray-200 p-2 dark:border-gray-700">
    <div className="flex items-center justify-between">
      <h4 className="font-medium">{t('hiddenShadow.gateTitle')}</h4>
      <button type="button" disabled={loading || busy} onClick={() => void reload()}
        className="text-blue-600 disabled:opacity-50 dark:text-blue-400">{t('shadowReview.refresh')}</button>
    </div>
    {loading && <p>{t('hiddenShadow.gateLoading')}</p>}
    {loadError && <p role="alert" className="text-red-600">{loadError}</p>}
    {assessment && <>
      <p className={ready ? 'text-green-700 dark:text-green-300' : 'text-amber-700 dark:text-amber-300'}>
        {t(ready ? 'hiddenShadow.gateReady' : 'hiddenShadow.gateBlocked')}
      </p>
      {assessment.sample_count !== null && <p>{t('hiddenShadow.gateCounts', {
        completed: assessment.completed_cases ?? 0, total: assessment.sample_count,
      })}</p>}
      {assessment.blockers.length > 0 && <ul className="list-inside list-disc">
        {assessment.blockers.map(blocker => <li key={blocker}>{t(`hiddenShadow.gateBlockers.${blocker}`)}</li>)}
      </ul>}
      <p>{t('hiddenShadow.gateCaveat')}</p>
      {!signoff && ready && <div className="space-y-2 border-t border-gray-200 pt-2 dark:border-gray-700">
        <strong>{t('hiddenShadow.signoffRequest')}</strong>
        <p>{t('hiddenShadow.signoffPrivacy')}</p>
        <label className="block">{t('shadowReview.signoffEvidenceReference')}
          <input value={reference} maxLength={120} onChange={event => setReference(event.target.value)}
            className="mt-1 w-full rounded border border-gray-300 bg-white p-1 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <label className="block">{t('shadowReview.signoffLiveCases')}
          <input type="number" min={1} max={100000} value={liveCount}
            onChange={event => setLiveCount(Number(event.target.value))}
            className="mt-1 w-full rounded border border-gray-300 bg-white p-1 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <label className="block">{t('shadowReview.signoffRequestNote')}
          <textarea value={requestNote} maxLength={500} onChange={event => setRequestNote(event.target.value)}
            className="mt-1 w-full rounded border border-gray-300 bg-white p-1 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <button type="button" disabled={!validRequest || busy} onClick={() => void requestSignoff()}
          className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
          {t('shadowReview.signoffRequestButton')}
        </button>
      </div>}
    </>}
    {signoff && <div className="space-y-2 border-t border-gray-200 pt-2 dark:border-gray-700">
      <strong>{t(`shadowReview.signoffStatuses.${signoff.status}`)}</strong>
      <p>{t('shadowReview.signoffRecord', {
        reference: signoff.evidence_reference, count: signoff.live_case_count,
        date: new Date(signoff.created_at).toLocaleString(),
      })}</p>
      <p className="break-all">{t('shadowReview.signoffManifest')}: {signoff.manifest_sha256}</p>
      <p className="break-all">{t('shadowReview.signoffDigest')}: {signoff.review_evidence_sha256}</p>
      <p className="whitespace-pre-wrap break-words">{signoff.note}</p>
      {signoff.decision_note && <p>{t('shadowReview.signoffDecisionNote')}: {signoff.decision_note}</p>}
      {signoff.status === 'accepted' && (!ready || signoff.current_evidence_valid !== true)
        && <p role="alert" className="text-red-600">{t('shadowReview.signoffNowBlocked')}</p>}
      {signoff.status === 'pending' && signoff.requester_id === actorId && <p>{t('shadowReview.signoffOtherAdmin')}</p>}
      {signoff.status === 'pending' && signoff.requester_id !== actorId && <div className="space-y-2">
        <label className="block">{t('shadowReview.signoffDecisionNote')}
          <textarea value={decisionNote} maxLength={500} onChange={event => setDecisionNote(event.target.value)}
            className="mt-1 w-full rounded border border-gray-300 bg-white p-1 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={liveVerified} onChange={event => setLiveVerified(event.target.checked)} />
          {t('shadowReview.signoffLiveVerified')}
        </label>
        <div className="flex gap-2">
          <button type="button" disabled={busy || !ready || !liveVerified || decisionNote.trim().length < 5}
            onClick={() => void decide('accepted')} className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
            {t('shadowReview.signoffAccept')}
          </button>
          <button type="button" disabled={busy || decisionNote.trim().length < 5}
            onClick={() => void decide('rejected')} className="rounded border border-gray-300 px-2 py-1 disabled:opacity-50 dark:border-gray-700">
            {t('shadowReview.signoffReject')}
          </button>
        </div>
      </div>}
    </div>}
    {actionError && <p role="alert" className="text-red-600">{actionError}</p>}
  </section>;
}
