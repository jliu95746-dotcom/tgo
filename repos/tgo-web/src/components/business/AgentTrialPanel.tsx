import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RotateCw, Send, Square } from 'lucide-react';
import { useAgentTrialStore } from '@/stores/agentTrialStore';
import { useAgentTrialReviewStore } from '@/stores/agentTrialReviewStore';
import AgentTrialReviewPanel from './AgentTrialReviewPanel';
import AgentKnowledgeCatalogEditor from './AgentKnowledgeCatalogEditor';
import { useAuthStore } from '@/stores/authStore';
import type { AICollectionResponse } from '@/types';
import type { KnowledgeChannel } from '@/types/scenarioResources';

interface Props { agentId: string; collections?: AICollectionResponse[]; questions?: string[]; initialChannel?: KnowledgeChannel }
const channels: KnowledgeChannel[] = ['web', 'wecom_kf', 'internal', 'app', 'phone'];
const button = 'rounded-xl border px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50';

/** Shared by saved employee trials and the save-before-trial setup flow. */
export default function AgentTrialPanel({ agentId, collections = [], questions, initialChannel = 'web' }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const trial = useAgentTrialStore();
  const review = useAgentTrialReviewStore();
  const [message, setMessage] = useState('');
  const [channel, setChannel] = useState<KnowledgeChannel>(initialChannel);
  const questionId = useId();
  const channelId = useId();
  const select = trial.select;
  const leave = trial.leave;
  const recover = trial.recover;
  useEffect(() => {
    if (user?.role === 'admin') void select(agentId);
    return () => leave(agentId);
  }, [agentId, user?.id, user?.project_id, user?.role, select, leave]);
  const scoped = trial.agentId === agentId && trial.identity === `${user?.project_id}:${user?.id}`;
  const pending = scoped ? trial.pending : null;
  const report = scoped ? trial.report : null;
  const busy = trial.submitting || trial.cancelling || trial.recovering;
  useEffect(() => {
    if (!scoped || report?.status !== 'running' || busy) return;
    const timer = window.setInterval(() => { void recover(); }, 2000);
    return () => window.clearInterval(timer);
  }, [scoped, report?.status, busy, recover]);
  if (user?.role !== 'admin') return null;
  const cases = review.agentId === agentId && review.identity === `${user.project_id}:${user.id}` ? review.catalog : [];
  const customCases = cases.some(row => row.id.startsWith('kc.'));
  const presets = customCases ? cases.map(row => row.question) : questions?.length ? questions : cases.length ? [...new Set(cases.map(row => row.question))]
    : ['material', 'size', 'policy'].map(key => t(`businessScenarios.trial.questions.${key}`));
  const blocked = !scoped || !trial.historyReady || Boolean(pending) || trial.loading || busy;
  return <div className="space-y-5">
    <p className="rounded-2xl bg-blue-50 p-4 text-sm leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-200">{t('businessScenarios.trial.hint')}</p>
    <AgentKnowledgeCatalogEditor agentId={agentId} disabled={Boolean(pending) || busy} />
    <div>
      <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-200">{t('businessScenarios.trial.presets')}</p>
      <div className="flex flex-wrap gap-2">{presets.map(question => <button key={question} type="button" disabled={blocked} onClick={() => setMessage(question)} className={`${button} border-blue-100 bg-blue-50 text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-200`}>{question}</button>)}</div>
    </div>
    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200" htmlFor={questionId}>{t('businessScenarios.trial.question')}
      <textarea id={questionId} value={message} onChange={event => setMessage(event.target.value)} disabled={blocked} maxLength={4000} rows={3} placeholder={t('businessScenarios.trial.placeholder')} className="mt-2 w-full resize-y rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-normal text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
    </label>
    <div className="flex flex-wrap items-end gap-3">
      <label htmlFor={channelId} className="text-sm text-gray-600 dark:text-gray-300">{t('businessScenarios.trial.channel')}
        <select id={channelId} value={channel} disabled={blocked} onChange={event => setChannel(event.target.value as KnowledgeChannel)} className="mt-1 block rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800">{channels.map(value => <option value={value} key={value}>{t(`businessScenarios.trial.channels.${value}`)}</option>)}</select>
      </label>
      <button type="button" onClick={() => void trial.start(message, channel)} disabled={blocked || !message.trim()} className={`${button} flex items-center gap-2 border-blue-600 bg-blue-600 text-white hover:bg-blue-700`}><Send size={16} />{t('businessScenarios.trial.start')}</button>
      {pending && <button type="button" onClick={() => void trial.cancel()} disabled={trial.cancelling || trial.recovering} className={`${button} flex items-center gap-2 border-gray-200 text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800`}><Square size={14} />{t(`businessScenarios.trial.${trial.cancelling ? 'cancelling' : 'cancel'}`)}</button>}
    </div>
    {scoped && <div aria-live="polite" className="space-y-3">
      {trial.submitting && <p className="flex items-center gap-2 text-sm text-blue-600 dark:text-blue-300"><Loader2 size={16} className="animate-spin" />{t('businessScenarios.trial.working')}</p>}
      {trial.error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">{t(`businessScenarios.trial.errors.${trial.error}`)}</p>}
      {pending && <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
        <p>{t(`businessScenarios.trial.${trial.unknown ? 'unknown' : 'pending'}`)}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => void trial.recover()} disabled={trial.recovering || trial.cancelling} className={`${button} border-amber-200 dark:border-amber-800`}>{t('businessScenarios.trial.recover')}</button>
          {trial.unknown && pending.request && <button type="button" onClick={() => void trial.retry()} disabled={busy || trial.loading} className={`${button} border-amber-200 dark:border-amber-800`}>{t('businessScenarios.trial.retry')}</button>}
        </div>
      </div>}
    </div>}
    {report && <section className="space-y-4 rounded-2xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
      <div className="flex flex-wrap gap-2 text-xs font-medium">
        <span className="rounded-full bg-blue-100 px-3 py-1 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200">{t(`businessScenarios.trial.status.${report.status}`)}</span>
        <span className="rounded-full bg-gray-200 px-3 py-1 text-gray-600 dark:bg-gray-700 dark:text-gray-200">{t('businessScenarios.trial.assessment')}</span>
      </div>
      <div className={`rounded-xl border p-3 text-sm ${report.applicability?.status === 'current' ? 'border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-200' : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200'}`}>
        <p className="font-medium">{t(`businessScenarios.trial.applicability.${report.applicability?.status ?? 'not_recorded'}`)}</p>
        <p className="mt-1 text-xs leading-6">{t('businessScenarios.trial.applicability.scope')}</p>
        {report.applicability?.checked_at && <p className="mt-1 text-xs">{t('businessScenarios.trial.applicability.checked', { time: new Date(report.applicability.checked_at).toLocaleString('zh-CN') })}</p>}
      </div>
      <div><h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('businessScenarios.trial.questionUsed')}</h4><p className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-600 dark:text-gray-300">{report.question}</p></div>
      {report.answer && <div><h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('businessScenarios.trial.answer')}</h4><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7 text-gray-700 dark:text-gray-200">{report.answer}</p></div>}
      {report.error_message && <p className="text-sm text-amber-700 dark:text-amber-200">{t('businessScenarios.trial.failure', { message: report.error_message })}</p>}
      <div><h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('businessScenarios.trial.sources')}</h4>
        {report.sources.length > 0 && <a href="/knowledge" target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex rounded-lg py-2 text-sm font-medium text-blue-600 hover:text-blue-700 dark:text-blue-300">{t('businessScenarios.review.openKnowledge')}</a>}
        {!report.sources.length && report.status !== 'running' && <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">{t('businessScenarios.trial.noSources')}</p>}
        <ul className="mt-2 space-y-2">{report.sources.map((source, index) => <li key={`${source.document_id}:${source.content_digest}`} className="rounded-xl bg-white p-3 text-sm dark:bg-gray-900">
          <p className="font-medium text-gray-800 dark:text-gray-100">{t('businessScenarios.trial.source', { number: index + 1 })}</p>
          <p className="mt-1 break-words text-gray-600 dark:text-gray-300">{t('businessScenarios.trial.collection', { name: collections.find(value => value.id === source.collection_id)?.display_name ?? source.collection_id })}</p>
          <details className="mt-2 text-xs text-gray-500 dark:text-gray-400"><summary className="cursor-pointer">{t('businessScenarios.trial.sourceDetails')}</summary><p className="mt-2 break-all">{t('businessScenarios.trial.document')}: {source.document_id}</p><p className="mt-1 break-all">{t('businessScenarios.trial.digest')}: {source.content_digest}</p></details>
        </li>)}</ul>
      </div>
      <p className="text-xs leading-6 text-gray-500 dark:text-gray-400">{t('businessScenarios.trial.assessmentHint')}</p>
    </section>}
    <AgentTrialReviewPanel agentId={agentId} report={report} />
    <section className="border-t border-gray-100 pt-4 dark:border-gray-800">
      <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('businessScenarios.trial.history')}</h4><button type="button" disabled={!scoped || trial.loading} onClick={() => void trial.refresh()} className={`${button} flex items-center gap-2 border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300`}><RotateCw size={14} />{t('businessScenarios.trial.refresh')}</button></div>
      {scoped && trial.loading && <p className="mt-3 text-sm text-gray-500">{t('businessScenarios.trial.loading')}</p>}
      {scoped && !trial.loading && !trial.reports.length && <p className="mt-3 text-sm text-gray-500">{t('businessScenarios.trial.empty')}</p>}
      {scoped && <ul className="mt-3 max-h-48 space-y-2 overflow-y-auto">{trial.reports.map(value => <li key={value.id}><button type="button" disabled={Boolean(pending) || busy} onClick={() => trial.inspect(value.id)} className="w-full rounded-xl border border-gray-100 px-3 py-2 text-left text-sm hover:bg-blue-50 disabled:opacity-60 dark:border-gray-800 dark:hover:bg-blue-900/20"><span className="block truncate text-gray-800 dark:text-gray-100">{value.question}</span><span className="mt-1 block text-xs text-gray-500">{t(`businessScenarios.trial.status.${value.status}`)} · {new Date(value.created_at).toLocaleString('zh-CN')}</span></button></li>)}</ul>}
    </section>
  </div>;
}
