import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { APIError } from '@/services/api';
import { hiddenShadowApi } from '@/services/hiddenShadowApi';
import { useAuthStore } from '@/stores/authStore';
import type {
  HiddenShadowAdjudicationVerdict, HiddenShadowReviewPair,
} from '@/types/hiddenShadow';
import type { ShadowReason } from '@/types/shadowReview';

interface Props {
  protocolId: string;
  candidateId: string;
}

const verdicts: HiddenShadowAdjudicationVerdict[] = [
  'acceptable', 'minor_issue', 'severe_error',
];
const reasons: ShadowReason[] = [
  'wrong_fact', 'privacy', 'unsafe_action', 'missing_handoff', 'other',
];

/** Pair is requested only after the entire frozen sample has two reviews. */
export default function HiddenShadowAdjudication({ protocolId, candidateId }: Props) {
  const { t } = useTranslation();
  const actorId = useAuthStore(state => state.user?.id);
  const [pair, setPair] = useState<HiddenShadowReviewPair | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<HiddenShadowAdjudicationVerdict | ''>('');
  const [reason, setReason] = useState<ShadowReason | ''>('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const retry = useRef<{ fingerprint: string; requestId: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setPair(null);
    setLoadError(null);
    void hiddenShadowApi.reviewPair(protocolId, candidateId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPair(value); })
      .catch(error => {
        if (controller.signal.aborted) return;
        setLoadError(error instanceof APIError
          ? `${error.getUserMessage()} (${error.getErrorCode()})`
          : t('hiddenShadow.pairFailed'));
      });
    return () => controller.abort();
  }, [protocolId, candidateId, t]);

  const disputed = pair && pair.reviews[0].verdict !== pair.reviews[1].verdict;
  const insufficient = pair?.reviews.some(row => row.verdict === 'insufficient_evidence');
  const reviewerCannotDecide = pair?.reviews.some(row => row.reviewer_id === actorId);
  const needsReason = verdict === 'minor_issue' || verdict === 'severe_error';
  const valid = Boolean(pair && disputed && !insufficient && !pair.adjudication
    && !reviewerCannotDecide && verdict && note.trim()
    && (!needsReason || reason) && (reason !== 'other' || note.trim().length >= 5));

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!valid || !pair || !verdict || saving) return;
    const fingerprint = JSON.stringify([
      protocolId, candidateId, pair.review_set_sha256,
      verdict, reason, note.trim(),
    ]);
    if (retry.current?.fingerprint !== fingerprint) {
      retry.current = { fingerprint, requestId: crypto.randomUUID() };
    }
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await hiddenShadowApi.submitAdjudication(
        protocolId, candidateId, {
          request_id: retry.current.requestId,
          expected_review_sha256: pair.review_set_sha256,
          verdict,
          reason_code: reason || null,
          note: note.trim(),
        },
      );
      setPair({ ...pair, adjudication: saved });
    } catch (error) {
      setSaveError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.adjudicationFailed'));
    } finally { setSaving(false); }
  }

  return <section className="mt-3 space-y-2 border-t border-gray-200 pt-2 dark:border-gray-700">
    <h5 className="font-medium">{t('hiddenShadow.pairTitle')}</h5>
    {!pair && !loadError && <p>{t('hiddenShadow.pairLoading')}</p>}
    {loadError && <p role="alert" className="text-red-600">{loadError}</p>}
    {pair && <>
      {pair.reviews.map((row, index) => <div key={row.id} className="rounded border border-gray-200 p-2 dark:border-gray-700">
        <strong>{t('hiddenShadow.reviewer', { number: index + 1 })}: {t(`shadowReview.verdicts.${row.verdict}`)}</strong>
        {row.reason_code && <p>{t(`shadowReview.reasons.${row.reason_code}`)}</p>}
        {row.note && <p className="whitespace-pre-wrap break-words">{row.note}</p>}
      </div>)}
      {!disputed && <p>{t('hiddenShadow.pairAgreed')}</p>}
      {insufficient && <p>{t('hiddenShadow.pairInsufficient')}</p>}
      {pair.adjudication && <p role="status" className="text-green-700 dark:text-green-300">
        {t('hiddenShadow.adjudicationSaved', {
          verdict: t(`shadowReview.verdicts.${pair.adjudication.verdict}`),
        })}
      </p>}
      {disputed && !insufficient && !pair.adjudication && reviewerCannotDecide &&
        <p>{t('hiddenShadow.thirdReviewerRequired')}</p>}
      {disputed && !insufficient && !pair.adjudication && !reviewerCannotDecide &&
        <form onSubmit={event => { void submit(event); }} className="space-y-2">
          <p>{t('hiddenShadow.thirdReviewerRequired')}</p>
          <select aria-label={t('hiddenShadow.adjudicationVerdict')} required value={verdict}
            onChange={event => { setVerdict(event.target.value as HiddenShadowAdjudicationVerdict); setSaveError(null); }}
            className="w-full rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-700 dark:bg-gray-800">
            <option value="">{t('shadowReview.chooseVerdict')}</option>
            {verdicts.map(value => <option key={value} value={value}>{t(`shadowReview.verdicts.${value}`)}</option>)}
          </select>
          {verdict && <select aria-label={t('shadowReview.reason')} required={needsReason} value={reason}
            onChange={event => { setReason(event.target.value as ShadowReason); setSaveError(null); }}
            className="w-full rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-700 dark:bg-gray-800">
            <option value="">{t('shadowReview.chooseReason')}</option>
            {reasons.map(value => <option key={value} value={value}>{t(`shadowReview.reasons.${value}`)}</option>)}
          </select>}
          <textarea aria-label={t('hiddenShadow.adjudicationNote')} required maxLength={500} value={note}
            onChange={event => { setNote(event.target.value); setSaveError(null); }}
            placeholder={t('hiddenShadow.adjudicationNoteHint')}
            className="w-full rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-700 dark:bg-gray-800" />
          <button type="submit" disabled={!valid || saving} className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
            {t(saving ? 'shadowReview.saving' : 'hiddenShadow.saveAdjudication')}
          </button>
          {saveError && <p role="alert" className="text-red-600">{saveError}</p>}
        </form>}
    </>}
  </section>;
}
