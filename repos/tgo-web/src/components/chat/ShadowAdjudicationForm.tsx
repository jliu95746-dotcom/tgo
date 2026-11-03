import { useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import type {
  ShadowAdjudicationVerdict, ShadowReason, ShadowSampleItem,
} from '@/types/shadowReview';

interface Props {
  batchId: string;
  item: ShadowSampleItem;
  onSaved: () => void;
}

const reasons: ShadowReason[] = [
  'wrong_fact', 'privacy', 'unsafe_action', 'missing_handoff', 'other',
];
const verdicts: ShadowAdjudicationVerdict[] = [
  'acceptable', 'minor_issue', 'severe_error',
];

/** Records a separate decision for the exact disputed review snapshot. */
export default function ShadowAdjudicationForm({ batchId, item, onSaved }: Props) {
  const { t } = useTranslation();
  const [verdict, setVerdict] = useState<ShadowAdjudicationVerdict | ''>('');
  const [reason, setReason] = useState<ShadowReason | ''>('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failed, setFailed] = useState(false);
  const retry = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const needsReason = verdict === 'minor_issue' || verdict === 'severe_error';
  const valid = Boolean(
    item.review_set_sha256 && verdict && note.trim()
    && (!needsReason || reason) && (reason !== 'other' || note.trim().length >= 5),
  );

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!valid || !verdict || !item.review_set_sha256 || saving || saved) return;
    const fingerprint = JSON.stringify([
      batchId, item.draft_id, item.review_set_sha256, verdict, reason, note.trim(),
    ]);
    if (retry.current?.fingerprint !== fingerprint) {
      retry.current = { fingerprint, requestId: crypto.randomUUID() };
    }
    setSaving(true);
    setFailed(false);
    try {
      await messageAnalysisApiService.submitShadowAdjudication({
        request_id: retry.current.requestId,
        batch_id: batchId,
        draft_id: item.draft_id,
        visitor_id: item.visitor_id,
        expected_review_sha256: item.review_set_sha256,
        verdict,
        reason_code: reason || null,
        note: note.trim(),
      });
      setSaved(true);
      onSaved();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const edited = () => { setSaved(false); setFailed(false); };

  return <details className="mt-2 rounded-md border border-amber-300 p-2 dark:border-amber-700">
    <summary className="cursor-pointer font-medium text-amber-800 dark:text-amber-200">
      {t('shadowReview.adjudicationTitle')}
    </summary>
    <p className="mt-1 text-gray-600 dark:text-gray-300">
      {t('shadowReview.adjudicationIntro')}
    </p>
    <form onSubmit={event => { void submit(event); }} className="mt-2 space-y-2">
      <select aria-label={t('shadowReview.adjudicationVerdict')} required value={verdict}
        onChange={event => { setVerdict(event.target.value as ShadowAdjudicationVerdict); edited(); }}
        className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100">
        <option value="">{t('shadowReview.chooseVerdict')}</option>
        {verdicts.map(value => <option key={value} value={value}>{t(`shadowReview.verdicts.${value}`)}</option>)}
      </select>
      <select aria-label={t('shadowReview.reason')} required={needsReason} value={reason}
        onChange={event => { setReason(event.target.value as ShadowReason); edited(); }}
        className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100">
        <option value="">{t('shadowReview.chooseReason')}</option>
        {reasons.map(value => <option key={value} value={value}>{t(`shadowReview.reasons.${value}`)}</option>)}
      </select>
      <textarea aria-label={t('shadowReview.note')} value={note} maxLength={500} required
        onChange={event => { setNote(event.target.value); edited(); }}
        placeholder={t('shadowReview.adjudicationNotePlaceholder')}
        className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" />
      <button type="submit" disabled={!valid || saving || saved}
        className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-50">
        {saving ? t('shadowReview.saving') : saved ? t('shadowReview.saved') : t('shadowReview.saveAdjudication')}
      </button>
      {failed && <p role="alert" className="text-red-600">{t('shadowReview.adjudicationFailed')}</p>}
    </form>
  </details>;
}
