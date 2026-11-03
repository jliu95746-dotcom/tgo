import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { WorkflowApiService } from '@/services/workflowApi';
import type {
  WorkflowAfterSalesEvidenceFile,
  WorkflowAfterSalesIntakeCreate,
  WorkflowAfterSalesIntakeSummary,
  WorkflowAfterSalesReviewSummary,
} from '@/types/workflow';

interface Props {
  review: WorkflowAfterSalesReviewSummary;
}

const EMPTY_FIELDS = {
  order_reference: '', sku: '', request_reason: '', policy_reference: '',
};

export default function AfterSalesIntakePanel({ review }: Props) {
  const { t } = useTranslation();
  const copy = 'businessScenarios.workflow.approvals.intake';
  const [intake, setIntake] = useState<WorkflowAfterSalesIntakeSummary | null>(null);
  const [files, setFiles] = useState<WorkflowAfterSalesEvidenceFile[]>([]);
  const [fields, setFields] = useState(EMPTY_FIELDS);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [preview, setPreview] = useState<WorkflowAfterSalesIntakeCreate | null>(null);
  const [loading, setLoading] = useState(true);
  const [filesError, setFilesError] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setFilesError(false);
    setIntake(null);
    setFiles([]);
    Promise.allSettled([
      WorkflowApiService.getAftersalesIntake(review.id),
      WorkflowApiService.listAftersalesReviewFiles(review.id),
    ]).then(([intakeResult, filesResult]) => {
      if (cancelled) return;
      if (intakeResult.status === 'fulfilled') setIntake(intakeResult.value);
      else setError(t(`${copy}.loadError`));
      if (filesResult.status === 'fulfilled') setFiles(filesResult.value);
      else { setFiles([]); setFilesError(true); }
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [review.id, reload, t]);

  const toggleFile = (id: string) => {
    setSelectedFiles(current => current.includes(id)
      ? current.filter(value => value !== id)
      : current.length < 5 ? [...current, id] : current);
  };

  const showPreview = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    setPreview({
      review_digest: review.review_digest,
      order_reference: fields.order_reference.trim(),
      sku: fields.sku.trim(),
      request_reason: fields.request_reason.trim(),
      policy_reference: fields.policy_reference.trim(),
      evidence_file_ids: [...selectedFiles],
    });
  };

  const prepare = async () => {
    if (!preview || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      const receipt = await WorkflowApiService.prepareAftersalesIntake(review.id, preview);
      setIntake(receipt);
      setPreview(null);
    } catch {
      setError(t(`${copy}.saveError`));
    } finally {
      setSubmitting(false);
    }
  };

  const fileName = (id: string) => files.find(file => file.id === id)?.file_name ?? id;
  const details = (values: WorkflowAfterSalesIntakeCreate | WorkflowAfterSalesIntakeSummary) => (
    <dl className="mt-3 grid gap-2 text-sm text-gray-700 dark:text-gray-200">
      <div><dt className="font-semibold">{t(`${copy}.order`)}</dt><dd className="break-words">{values.order_reference}</dd></div>
      <div><dt className="font-semibold">{t(`${copy}.sku`)}</dt><dd className="break-words">{values.sku}</dd></div>
      <div><dt className="font-semibold">{t(`${copy}.reason`)}</dt><dd className="whitespace-pre-wrap break-words">{values.request_reason}</dd></div>
      <div><dt className="font-semibold">{t(`${copy}.policy`)}</dt><dd className="break-words">{values.policy_reference}</dd></div>
      <div><dt className="font-semibold">{t(`${copy}.evidence`)}</dt><dd className="break-words">{values.evidence_file_ids.length ? values.evidence_file_ids.map(fileName).join('、') : t(`${copy}.noEvidence`)}</dd></div>
    </dl>
  );

  return <div className="mt-4 rounded-xl border border-purple-200 bg-purple-50 p-4 dark:border-purple-800 dark:bg-purple-950/20">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t(`${copy}.title`)}</p>
      <button type="button" onClick={() => setReload(value => value + 1)} disabled={loading || submitting}
        className="rounded-lg border border-purple-300 px-3 py-1 text-xs text-purple-700 disabled:opacity-50 dark:border-purple-700 dark:text-purple-300">
        {t(`${copy}.refresh`)}
      </button>
    </div>
    <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.scope`)}</p>
    {loading && <p className="mt-3 text-sm text-gray-500">{t(`${copy}.loading`)}</p>}
    {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}
    {filesError && <p role="alert" className="mt-3 text-sm text-amber-700 dark:text-amber-300">{t(`${copy}.filesError`)}</p>}
    {!loading && intake && <>
      <p role="status" className="mt-3 text-sm font-semibold text-emerald-700 dark:text-emerald-300">{t(`${copy}.receipt`)}</p>
      <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.preparedAt`)} {new Date(intake.prepared_at).toLocaleString()}</p>
      <p className="mt-1 break-all text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.receiptId`)} {intake.review_id}</p>
      {details(intake)}
      <p className="mt-3 text-xs font-semibold text-amber-700 dark:text-amber-300">{t(`${copy}.notSubmitted`)}</p>
    </>}
    {!loading && !intake && !error && !preview && <form onSubmit={showPreview} className="mt-3 space-y-3">
      {(['order_reference', 'sku', 'request_reason', 'policy_reference'] as const).map(key => (
        <label key={key} className="block text-xs font-semibold text-gray-700 dark:text-gray-200">
          {t(`${copy}.${{ order_reference: 'order', sku: 'sku', request_reason: 'reason', policy_reference: 'policy' }[key]}`)}
          {key === 'request_reason'
            ? <textarea required minLength={5} maxLength={500} value={fields[key]}
                onChange={event => setFields(current => ({ ...current, [key]: event.target.value }))}
                className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-normal text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
            : <input required maxLength={key === 'policy_reference' ? 200 : 100} value={fields[key]}
                onChange={event => setFields(current => ({ ...current, [key]: event.target.value }))}
                className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-normal text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />}
        </label>
      ))}
      <p className="text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.privacyHint`)}</p>
      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold text-gray-700 dark:text-gray-200">{t(`${copy}.evidence`)}</legend>
        {files.length === 0 && <p className="text-xs text-gray-600 dark:text-gray-300">{t(`${copy}.noFiles`)}</p>}
        {files.map(file => <label key={file.id} className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-200">
          <input type="checkbox" checked={selectedFiles.includes(file.id)} onChange={() => toggleFile(file.id)}
            disabled={!selectedFiles.includes(file.id) && selectedFiles.length >= 5} className="mt-0.5 accent-purple-600" />
          <span className="break-words">{file.file_name}</span>
        </label>)}
      </fieldset>
      <button type="submit" disabled={filesError}
        className="rounded-lg bg-purple-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{t(`${copy}.preview`)}</button>
    </form>}
    {!loading && !intake && preview && <div className="mt-3">
      <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">{t(`${copy}.confirmTitle`)}</p>
      {details(preview)}
      <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">{t(`${copy}.notSubmitted`)}</p>
      <div className="mt-3 flex gap-2">
        <button type="button" disabled={submitting} onClick={() => setPreview(null)}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700">{t(`${copy}.back`)}</button>
        <button type="button" disabled={submitting} onClick={() => void prepare()}
          className="rounded-lg bg-purple-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">{t(`${copy}.confirm`)}</button>
      </div>
    </div>}
  </div>;
}
