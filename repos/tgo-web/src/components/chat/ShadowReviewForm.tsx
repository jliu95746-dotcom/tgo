import { useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import { APIError } from '@/services/api';
import type { ShadowReason, ShadowVerdict } from '@/types/shadowReview';

interface Props {
  draftId: string;
  visitorId: string;
  onSaved: () => void;
}

const reasons: ShadowReason[] = [
  'wrong_fact', 'privacy', 'unsafe_action', 'missing_handoff', 'other',
];
const verdicts: ShadowVerdict[] = [
  'acceptable', 'minor_issue', 'severe_error', 'insufficient_evidence',
];

/** Records an assessment only; it never approves or sends a customer reply. */
export default function ShadowReviewForm({ draftId, visitorId, onSaved }: Props) {
  const { t } = useTranslation();
  const [verdict, setVerdict] = useState<ShadowVerdict | ''>('');
  const [reason, setReason] = useState<ShadowReason | ''>('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failed, setFailed] = useState(false);
  const [failureCode, setFailureCode] = useState<string | null>(null);
  const retry = useRef<{ fingerprint: string; requestId: string } | null>(null);

  const needsReason = verdict === 'minor_issue' || verdict === 'severe_error';
  const valid = Boolean(verdict && (!needsReason || reason) && (reason !== 'other' || note.trim()));

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!valid || !verdict || saving || saved) return;
    const fingerprint = JSON.stringify([draftId, visitorId, verdict, reason, note.trim()]);
    if (retry.current?.fingerprint !== fingerprint) {
      retry.current = { fingerprint, requestId: crypto.randomUUID() };
    }
    setSaving(true);
    setFailed(false);
    setFailureCode(null);
    try {
      await messageAnalysisApiService.submitShadowReview({
        draft_id: draftId,
        visitor_id: visitorId,
        request_id: retry.current.requestId,
        verdict,
        reason_code: reason || null,
        note: note.trim() || null,
      });
      setSaved(true);
      onSaved();
    } catch (error) {
      setFailureCode(error instanceof APIError ? error.getErrorCode() : null);
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const edited = () => { setSaved(false); setFailed(false); setFailureCode(null); };

  return <form onSubmit={event => { void submit(event); }} className="mt-2 space-y-2 rounded-md border border-gray-200 p-2 dark:border-gray-600">
    <p className="text-xs font-medium text-gray-700 dark:text-gray-200">{t('shadowReview.assessment')}</p>
    <select aria-label={t('shadowReview.verdict')} required value={verdict}
      onChange={event => { setVerdict(event.target.value as ShadowVerdict); edited(); }}
      className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100">
      <option value="">{t('shadowReview.chooseVerdict')}</option>
      {verdicts.map(value => <option key={value} value={value}>{t(`shadowReview.verdicts.${value}`)}</option>)}
    </select>
    {verdict && <select aria-label={t('shadowReview.reason')} required={needsReason} value={reason}
      onChange={event => { setReason(event.target.value as ShadowReason); edited(); }}
      className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100">
      <option value="">{t('shadowReview.chooseReason')}</option>
      {reasons.map(value => <option key={value} value={value}>{t(`shadowReview.reasons.${value}`)}</option>)}
    </select>}
    {verdict && <textarea aria-label={t('shadowReview.note')} value={note} maxLength={500}
      required={reason === 'other'} onChange={event => { setNote(event.target.value); edited(); }}
      placeholder={t('shadowReview.notePlaceholder')}
      className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" />}
    <button type="submit" disabled={!valid || saving || saved}
      className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-50">
      {saving ? t('shadowReview.saving') : saved ? t('shadowReview.saved') : t('shadowReview.saveReview')}
    </button>
    {failed && <p role="alert" className="text-xs text-red-600">{t(
      failureCode === 'SHADOW_REVIEW_COORDINATOR_CONFLICT'
        ? 'shadowReview.coordinatorCannotReview'
        : failureCode === 'SHADOW_REVIEW_ALREADY_SUBMITTED'
          ? 'shadowReview.officialReviewImmutable'
          : 'shadowReview.saveFailed',
    )}</p>}
  </form>;
}
