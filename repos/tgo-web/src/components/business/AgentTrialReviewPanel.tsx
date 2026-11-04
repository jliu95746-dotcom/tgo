import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Loader2, RotateCw, XCircle } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useAgentTrialReviewStore } from '@/stores/agentTrialReviewStore';
import { useAgentActivationStore } from '@/stores/agentActivationStore';
import type { AgentTrialReport } from '@/types/agentTrials';
import type { KnowledgeReviewChecks, KnowledgeReviewState } from '@/types/agentTrialReviews';

interface Props { agentId: string; report: AgentTrialReport | null }
const emptyChecks: KnowledgeReviewChecks = { answer_correct: false, evidence_checked: false, no_unverified_claims: false, expected_behavior: false };
const button = 'rounded-xl border px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50';
const time = (value: string) => new Date(value).toLocaleString('zh-CN');

function ReviewForm({ state, disabled }: { state: KnowledgeReviewState; disabled: boolean }) {
  const { t } = useTranslation();
  const submitReview = useAgentTrialReviewStore(store => store.submit);
  const submit: typeof submitReview = async (...args) => {
    const actor = useAuthStore.getState().user;
    useAgentActivationStore.getState().invalidate(state.agent_id);
    await submitReview(...args);
    const current = useAuthStore.getState().user;
    if (current?.id === actor?.id && current?.project_id === actor?.project_id && current?.role === actor?.role) useAgentActivationStore.getState().invalidate(state.agent_id);
  };
  const [caseId, setCaseId] = useState(state.available_cases[0]?.id ?? '');
  const [checks, setChecks] = useState<KnowledgeReviewChecks>({ ...emptyChecks });
  const [note, setNote] = useState('');
  const labelId = useId(), noteId = useId();
  const spec = state.available_cases.find(row => row.id === caseId);
  if (!spec) return <p className="text-sm text-amber-700 dark:text-amber-200">{t('businessScenarios.review.noCase')}</p>;
  const canPass = !disabled && state.can_review && Date.parse(state.valid_until) > Date.now() && Object.values(checks).every(Boolean);
  return <div className="space-y-4">
    <label htmlFor={labelId} className="block text-sm font-medium text-gray-700 dark:text-gray-200">{t('businessScenarios.review.case')}
      <select id={labelId} value={caseId} disabled={disabled} onChange={event => { setCaseId(event.target.value); setChecks({ ...emptyChecks }); setNote(''); }} className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800">
        {state.available_cases.map(row => <option value={row.id} key={row.id}>{row.question}</option>)}
      </select>
    </label>
    <p className="rounded-xl bg-blue-50 p-3 text-sm leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-200">{t(`businessScenarios.review.expected.${spec.expected_result}`)}</p>
    {!state.can_review && <p className="text-sm leading-6 text-amber-700 dark:text-amber-200">{t('businessScenarios.review.notReady')}</p>}
    <fieldset disabled={disabled} className="space-y-3">
      <legend className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-100">{t('businessScenarios.review.checkTitle')}</legend>
      {(Object.keys(emptyChecks) as (keyof KnowledgeReviewChecks)[]).map(key => <label key={key} className="flex items-start gap-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
        <input type="checkbox" checked={checks[key]} onChange={event => setChecks(previous => ({ ...previous, [key]: event.target.checked }))} className="mt-1 h-4 w-4 shrink-0 accent-blue-600" />
        <span>{t(`businessScenarios.review.checks.${key}`)}</span>
      </label>)}
    </fieldset>
    <label htmlFor={noteId} className="block text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.review.note')}
      <textarea id={noteId} value={note} maxLength={1000} rows={2} disabled={disabled} onChange={event => setNote(event.target.value)} placeholder={t('businessScenarios.review.noteHint')} className="mt-2 w-full rounded-xl border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-800" />
    </label>
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={!canPass} onClick={() => void submit(caseId, 'passed', checks, note)} className={`${button} flex items-center gap-2 border-blue-600 bg-blue-600 text-white hover:bg-blue-700`}><CheckCircle2 size={16} />{t('businessScenarios.review.pass')}</button>
      <button type="button" disabled={disabled} onClick={() => void submit(caseId, 'rejected', checks, note)} className={`${button} flex items-center gap-2 border-gray-200 text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800`}><XCircle size={16} />{t('businessScenarios.review.reject')}</button>
    </div>
  </div>;
}

/** Review uses the report's immutable answer and the authenticated owner's cases. */
export default function AgentTrialReviewPanel({ agentId, report }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(store => store.user);
  const review = useAgentTrialReviewStore();
  const select = review.select, leave = review.leave;
  const reportId = report?.id ?? null, question = report?.question ?? '';
  useEffect(() => {
    if (user?.role === 'admin') void select(agentId, reportId, question);
    return () => leave(agentId);
  }, [agentId, reportId, question, report?.status, report?.applicability?.checked_at, user?.id, user?.project_id, user?.role, select, leave]);
  if (!report || user?.role !== 'admin') return null;
  const scoped = review.agentId === agentId && review.reportId === report.id && review.identity === `${user.project_id}:${user.id}`;
  if (!scoped) return null;
  const state = review.state;
  const disabled = !review.ready || review.loading || review.submitting || Boolean(review.pending) || report.status === 'running';
  return <section className="space-y-4 rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4 className="font-semibold text-gray-900 dark:text-gray-100">{t('businessScenarios.review.title')}</h4>
      <button type="button" onClick={() => void review.refresh()} disabled={review.loading || review.submitting} className={`${button} flex items-center gap-2 border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300`}><RotateCw size={14} />{t('businessScenarios.review.refresh')}</button>
    </div>
    <p className="text-xs leading-6 text-gray-500 dark:text-gray-400">{t('businessScenarios.review.scope')}</p>
    <div aria-live="polite" className="text-sm">
      {(review.loading || review.submitting) && <p className="flex items-center gap-2 text-blue-600 dark:text-blue-300"><Loader2 size={16} className="animate-spin" />{t(`businessScenarios.review.${review.submitting ? 'saving' : 'loading'}`)}</p>}
      {review.error && <p role="alert" className="mt-2 text-amber-700 dark:text-amber-200">{t(`businessScenarios.review.errors.${review.error}`)}</p>}
    </div>
    {review.pending && <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
      <p>{t(`businessScenarios.review.${review.unknown ? 'unknown' : 'pending'}`)}</p>
      {review.unknown && <button type="button" onClick={() => void review.retry()} disabled={review.loading || review.submitting} className={`${button} border-amber-200 dark:border-amber-800`}>{t('businessScenarios.review.retry')}</button>}
    </div>}
    {state && <>
      <p className="text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.review.validUntil', { time: time(state.valid_until) })}</p>
      <ReviewForm key={`${report.id}:${state.available_cases.map(row => row.id).join(',')}:${state.applicability.status}:${state.reviews[0]?.id ?? ''}`} state={state} disabled={disabled} />
      <div className="space-y-3 border-t border-gray-100 pt-4 dark:border-gray-800">
        <h5 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('businessScenarios.review.history')}</h5>
        {!state.reviews.length && <p className="text-sm text-gray-500 dark:text-gray-400">{t('businessScenarios.review.empty')}</p>}
        {state.reviews.map(row => <article key={row.id} className="space-y-2 rounded-xl bg-gray-50 p-3 text-sm dark:bg-gray-800">
          <div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-3 py-1 text-xs ${row.effective && review.ready && Date.parse(row.valid_until) > Date.now() ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-200' : 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-200'}`}>{t(`businessScenarios.review.decisions.${row.decision}`)}</span>
            <span className="text-xs text-gray-500 dark:text-gray-400">{t(`businessScenarios.review.${row.superseded ? 'superseded' : row.effective && review.ready && Date.parse(row.valid_until) > Date.now() ? 'effective' : 'notEffective'}`)}</span>
          </div>
          <p className="break-words text-gray-700 dark:text-gray-200">{row.case.question}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.review.recordTime', { revision: row.revision, time: time(row.created_at) })}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.review.validUntil', { time: time(row.valid_until) })}</p>
          {row.note && <p className="whitespace-pre-wrap break-words text-gray-600 dark:text-gray-300">{row.note}</p>}
        </article>)}
      </div>
    </>}
  </section>;
}
