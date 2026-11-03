import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { APIError } from '@/services/api';
import { hiddenShadowApi } from '@/services/hiddenShadowApi';
import type {
  HiddenShadowCaseDetail, HiddenShadowReviewReceipt,
  HiddenShadowSourceMessage,
} from '@/types/hiddenShadow';
import type { ShadowReason, ShadowVerdict } from '@/types/shadowReview';
import HiddenShadowAdjudication from './HiddenShadowAdjudication';

interface Props {
  protocolId: string;
  candidateId: string;
  reviewComplete: boolean;
  onSaved: () => void;
}

const verdicts: ShadowVerdict[] = [
  'acceptable', 'minor_issue', 'severe_error', 'insufficient_evidence',
];
const reasons: ShadowReason[] = [
  'wrong_fact', 'privacy', 'unsafe_action', 'missing_handoff', 'other',
];

/** Shows one frozen private comparison and records only this administrator's verdict. */
export default function HiddenShadowCaseReview({ protocolId, candidateId, reviewComplete, onSaved }: Props) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<HiddenShadowCaseDetail | null>(null);
  const [source, setSource] = useState<HiddenShadowSourceMessage | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<ShadowVerdict | ''>('');
  const [reason, setReason] = useState<ShadowReason | ''>('');
  const [note, setNote] = useState('');
  const [sourceChecked, setSourceChecked] = useState(false);
  const [saving, setSaving] = useState(false);
  const [receipt, setReceipt] = useState<HiddenShadowReviewReceipt | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const retry = useRef<{ fingerprint: string; requestId: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setSource(null);
    setReceipt(null);
    setLoadError(null);
    setSourceChecked(false);
    void Promise.all([
      hiddenShadowApi.caseDetail(protocolId, candidateId, controller.signal),
      hiddenShadowApi.myReview(protocolId, candidateId, controller.signal),
      hiddenShadowApi.sourceMessage(protocolId, candidateId, controller.signal),
    ])
      .then(([value, ownReview, sourceMessage]) => {
        if (controller.signal.aborted) return;
        if (
          sourceMessage.protocol_id !== value.protocol_id
          || sourceMessage.candidate_id !== value.candidate_id
          || sourceMessage.source_message_id !== value.source_message_id
          || sourceMessage.content_sha256 !== value.source_content_sha256
        ) {
          setLoadError(t('hiddenShadow.sourceMismatch'));
          return;
        }
        setDetail(value);
        setSource(sourceMessage);
        setReceipt(ownReview);
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setLoadError(error instanceof APIError
          ? `${error.getUserMessage()} (${error.getErrorCode()})`
          : t('hiddenShadow.detailFailed'));
      });
    return () => controller.abort();
  }, [protocolId, candidateId, t]);

  const needsReason = verdict === 'minor_issue' || verdict === 'severe_error';
  const valid = Boolean(
    detail && source && sourceChecked && verdict && (!needsReason || reason)
    && (reason !== 'other' || note.trim()),
  );

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!valid || !verdict || saving || receipt) return;
    const fingerprint = JSON.stringify([
      protocolId, candidateId, verdict, reason, note.trim(),
    ]);
    if (retry.current?.fingerprint !== fingerprint) {
      retry.current = { fingerprint, requestId: crypto.randomUUID() };
    }
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await hiddenShadowApi.submitReview(protocolId, candidateId, {
        request_id: retry.current.requestId,
        verdict,
        reason_code: reason || null,
        note: note.trim() || null,
      });
      setReceipt(saved);
      onSaved();
    } catch (error) {
      setSaveError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.reviewFailed'));
    } finally { setSaving(false); }
  }

  return <section className="mt-3 space-y-2 rounded border border-blue-300 bg-white p-3 dark:border-blue-800 dark:bg-gray-900">
    <h4 className="font-medium">{t('hiddenShadow.reviewCase')}</h4>
    {!detail && !loadError && <p>{t('hiddenShadow.detailLoading')}</p>}
    {loadError && <p role="alert" className="text-red-600">{loadError}</p>}
    {detail && source && <>
      <p className="break-all text-gray-500">{t('hiddenShadow.sourceId')}: {detail.source_message_id}</p>
      <div className="rounded border border-gray-200 bg-gray-50 p-2 dark:border-gray-700 dark:bg-gray-800">
        <strong>{t('hiddenShadow.sourceContent')}</strong>
        <p className="whitespace-pre-wrap break-words">{source.content}</p>
        <p className="break-all text-xs text-gray-500">{t('hiddenShadow.sourceVerifiedAt')}: {new Date(source.verified_at).toLocaleString()}</p>
      </div>
      <p>{t('hiddenShadow.sourceHint')}{' '}
        <Link target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400"
          to={`/chat/251/${detail.visitor_id}-vtr`}>
          {t('hiddenShadow.openConversation')}
        </Link>
      </p>
      <div>
        <strong>{t('hiddenShadow.privateAnswer')}</strong>
        <p className="whitespace-pre-wrap break-words">{detail.answer}</p>
      </div>
      <div>
        <strong>{t('hiddenShadow.sentReply')}</strong>
        <p className="whitespace-pre-wrap break-words">{detail.staff_reply}</p>
      </div>
      <p className="break-all text-gray-500">{t('hiddenShadow.evidenceDigest')}: {detail.evidence_sha256}</p>
      {!receipt && <form onSubmit={event => { void submit(event); }} className="space-y-2 border-t border-gray-200 pt-2 dark:border-gray-700">
        <p>{t('hiddenShadow.blindReviewHint')}</p>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={sourceChecked}
            onChange={event => setSourceChecked(event.target.checked)} />
          {t('hiddenShadow.sourceChecked')}
        </label>
        <select aria-label={t('shadowReview.verdict')} required value={verdict}
          onChange={event => { setVerdict(event.target.value as ShadowVerdict); setSaveError(null); }}
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
        {verdict && <textarea aria-label={t('shadowReview.note')} maxLength={500} value={note}
          required={reason === 'other'} onChange={event => { setNote(event.target.value); setSaveError(null); }}
          placeholder={t('shadowReview.notePlaceholder')}
          className="w-full rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-700 dark:bg-gray-800" />}
        <button type="submit" disabled={!valid || saving} className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
          {t(saving ? 'shadowReview.saving' : 'shadowReview.saveReview')}
        </button>
        {saveError && <p role="alert" className="text-red-600">{saveError}</p>}
      </form>}
      {receipt && <div role="status" className="space-y-1 text-green-700 dark:text-green-300">
        <p>{t('hiddenShadow.reviewSaved', { verdict: t(`shadowReview.verdicts.${receipt.verdict}`) })}</p>
        {receipt.source_verified_at
          ? <p>{t('hiddenShadow.reviewProofSaved')}: {new Date(receipt.source_verified_at).toLocaleString()}</p>
          : <p className="text-amber-700 dark:text-amber-300">{t('hiddenShadow.reviewProofMissing')}</p>}
      </div>}
      {reviewComplete && <HiddenShadowAdjudication protocolId={protocolId} candidateId={candidateId} />}
    </>}
  </section>;
}
