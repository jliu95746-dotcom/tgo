import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, RotateCw } from 'lucide-react';
import { useAgentActivationEntry } from '@/hooks/useAgentActivationEntry';
import { useAgentTrialReviewStore } from '@/stores/agentTrialReviewStore';
import { activationUsable, useAgentActivationStore } from '@/stores/agentActivationStore';
import { useAuthStore } from '@/stores/authStore';
import { useAIStore } from '@/stores/aiStore';
import { AIAgentsApiService } from '@/services/aiAgentsApi';
import AgentActivationPolicyEditor from './AgentActivationPolicyEditor';

interface Props {
  agentId: string;
  name: string;
  allowActivation?: boolean;
  onTrial?: () => void;
  onActivated?: () => void;
}
function hasPendingReview(agentId: string, scope: string | null): boolean {
  const review = useAgentTrialReviewStore.getState();
  return review.identity === scope && ((review.agentId === agentId && (review.submitting || Boolean(review.pending)))
    || Object.entries(review.sessions).some(([key, session]) => key.startsWith(`${agentId}:`) && Boolean(session.pending)));
}
export default function AgentActivationPanel({ agentId, name, allowActivation = true, onTrial, onActivated }: Props) {
  const { t } = useTranslation();
  const { entry, now, scope, load } = useAgentActivationEntry(agentId);
  const reviewPending = useAgentTrialReviewStore(state => state.identity === scope
    && ((state.agentId === agentId && (state.submitting || Boolean(state.pending)))
      || Object.entries(state.sessions).some(([key, session]) => key.startsWith(`${agentId}:`) && Boolean(session.pending))));
  const store = useAgentActivationStore();
  const agents = useAIStore(state => state.agents);
  const refreshAgents = useAIStore(state => state.refreshAgents);
  const [confirmation, setConfirmation] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [pauseFailed, setPauseFailed] = useState(false);
  const pausePending = useRef(false);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  const busy = Boolean(pausing || entry?.loading || entry?.writing || entry?.enabling);
  const ready = activationUsable(entry, now) && !reviewPending;
  const current = Boolean(entry?.readinessReady && entry.readiness && Date.parse(entry.readiness.checked_at) > now - 60000);
  const serving = agents.filter(agent => agent.id !== agentId && agent.status === 'active').map(agent => agent.name).join('、');
  const owns = () => {
    const user = useAuthStore.getState().user;
    return mounted.current && user?.role === 'admin' && `${user.project_id}:${user.id}` === scope;
  };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (confirmation) confirmButton.current?.focus(); }, [confirmation]);
  if (!scope) return null;
  const activate = async () => {
    if (!confirmation || !owns()) return;
    setConfirmation(false);
    const success = await store.enable(agentId, async () => {
      if (!owns() || hasPendingReview(agentId, scope)) throw Object.assign(new Error('identityChangedClosedOrReviewPending'), { status: 409 });
    });
    if (!owns()) return;
    await load(agentId, true);
    if (!success) return;
    try { await refreshAgents(); if (owns()) setRefreshFailed(Boolean(useAIStore.getState().agentsError)); } catch { if (owns()) setRefreshFailed(true); }
    if (owns()) onActivated?.();
  };
  const refresh = () => { void load(agentId, true); void store.loadTemplates(true); };
  const recover = async (retry = false) => {
    if (!owns()) return;
    if (retry) await store.retryActivation(agentId, async () => {
      if (!owns() || hasPendingReview(agentId, scope)) throw Object.assign(new Error('recoveryBlocked'), { status: 409 });
    });
    else await store.recover(agentId);
    if (!owns()) return;
    await load(agentId, true);
    if (owns()) await refreshAgents();
  };
  const pause = async () => {
    if (busy || entry?.pending || pausePending.current || !owns()) return;
    pausePending.current = true; setPausing(true); setPauseFailed(false);
    try {
      const result = await AIAgentsApiService.updateAgent(agentId, { is_active: false });
      if (!owns()) return;
      if (result.id !== agentId || result.is_active !== false) throw new Error('invalidPauseReceipt');
      store.invalidate();
      await load(agentId, true);
      if (owns()) try { await refreshAgents(); if (owns()) setRefreshFailed(Boolean(useAIStore.getState().agentsError)); } catch { if (owns()) setRefreshFailed(true); }
    } catch { if (owns()) { setPauseFailed(true); await load(agentId, true); } }
    finally { pausePending.current = false; if (owns()) setPausing(false); }
  };
  return <section className="space-y-4 rounded-2xl border border-gray-100 bg-white p-4 text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-200 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-base font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.activation.title')}</h3><button type="button" disabled={busy} onClick={refresh} className="flex items-center gap-1.5 text-xs font-medium text-blue-600 disabled:opacity-50 dark:text-blue-300"><RotateCw className={`h-3.5 w-3.5 ${entry?.loading ? 'animate-spin' : ''}`} />{t('businessScenarios.activation.refresh')}</button></div>
    <p className="text-xs leading-5 text-gray-500 dark:text-gray-400">{t('businessScenarios.activation.hint')}</p>
    <dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-xs text-gray-500">{t('businessScenarios.activation.enabledState')}</dt><dd className="mt-1 font-medium">{t(`businessScenarios.activation.${current ? entry?.readiness?.enabled ? 'serving' : 'paused' : 'unavailable'}`)}</dd></div><div><dt className="text-xs text-gray-500">{t('businessScenarios.activation.testState')}</dt><dd className={`mt-1 flex items-center gap-1.5 font-medium ${ready ? 'text-emerald-600 dark:text-emerald-400' : ''}`}>{ready && <CheckCircle2 size={16} />}{t(`businessScenarios.activation.${entry?.loading ? 'checking' : ready ? 'ready' : current ? 'blocked' : 'unavailable'}`)}</dd></div></dl>
    {current && entry?.readiness?.blockers.length !== 0 && <ul className="space-y-2 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">{entry?.readiness?.blockers.map((row, index) => <li key={`${row.code}:${row.channel}:${index}`}>{row.channel && `${t(`businessScenarios.activation.channels.${row.channel}`)}：`}{row.message}</li>)}</ul>}
    {current && <ul className="space-y-2">{entry?.readiness?.channels.map(row => <li key={row.channel} className="flex flex-wrap justify-between gap-2 rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-800"><span>{t(`businessScenarios.scenes.${row.template_id}.title`)} · {t(`businessScenarios.activation.channels.${row.channel}`)}</span><span>{t(`businessScenarios.activation.states.${row.status === 'passed' && (!row.valid_until || Date.parse(row.valid_until) <= now) ? 'needs_retest' : row.status}`)}{row.valid_until && ` · ${t('businessScenarios.activation.validUntil', { time: new Date(row.valid_until).toLocaleString() })}`}</span></li>)}</ul>}
    {entry?.error && <p role="alert" className="text-xs leading-5 text-red-600 dark:text-red-400">{t(`businessScenarios.activation.errors.${entry.error}`)}</p>}
    {reviewPending && <p role="alert" className="text-xs text-amber-700 dark:text-amber-300">{t('businessScenarios.activation.reviewPending')}</p>}
    {entry?.pending && entry.pending.kind !== 'enable' && <button type="button" disabled={busy} onClick={() => void store.retry(agentId).then(() => load(agentId, true))} className="text-xs font-medium text-blue-600 disabled:opacity-50 dark:text-blue-300">{t('businessScenarios.activation.retryOriginal')}</button>}
    {entry?.pending?.kind === 'enable' && <div className="space-y-3 rounded-xl bg-blue-50 p-3 text-xs leading-5 dark:bg-blue-900/20">
      <p>{t('businessScenarios.activation.recoveryHint')}</p>
      <button type="button" disabled={busy} onClick={() => void recover()} className="font-medium text-blue-600 disabled:opacity-50 dark:text-blue-300">{t('businessScenarios.activation.lookupOriginal')}</button>
      {entry.receipt?.status === 'not_found' && <><p>{t('businessScenarios.activation.notFoundHint')}</p><button type="button" disabled={busy || reviewPending} onClick={() => void recover(true)} className="rounded-xl border border-blue-200 px-3 py-2 font-medium text-blue-600 disabled:opacity-50 dark:border-blue-800 dark:text-blue-300">{t('businessScenarios.activation.retryActivation')}</button></>}
    </div>}
    {entry?.receipt?.status === 'completed' && <p className="text-xs text-gray-500">{t('businessScenarios.activation.originalCompleted')}</p>}
    {entry?.receipt?.status === 'rejected' && <p role="alert" className="text-xs text-amber-700 dark:text-amber-300">{t(`businessScenarios.activation.failure.${entry.receipt.failure_code}`)}</p>}
    {refreshFailed && <p role="alert" className="text-xs text-amber-700">{t('businessScenarios.activation.refreshFailed')}</p>}
    {pauseFailed && <p role="alert" className="text-xs text-red-600">{t('businessScenarios.activation.pauseFailed')}</p>}
    {current && entry?.readiness?.enabled && <button type="button" disabled={busy || Boolean(entry.pending) || confirmation} onClick={() => void pause()} className="rounded-xl border border-amber-200 px-4 py-2 text-sm font-medium text-amber-700 disabled:opacity-50 dark:border-amber-800 dark:text-amber-300">{t('businessScenarios.activation.pause')}</button>}
    <AgentActivationPolicyEditor agentId={agentId} disabled={confirmation || pausing} />
    {!allowActivation && <p className="text-xs text-gray-500">{t('businessScenarios.activation.saveBusinessFirst')}</p>}
    {onTrial && <button type="button" disabled={busy || confirmation} onClick={onTrial} className="rounded-xl border border-blue-200 px-4 py-2 text-sm font-medium text-blue-600 disabled:opacity-50 dark:border-blue-800 dark:text-blue-300">{t('businessScenarios.activation.trial')}</button>}
    {allowActivation && !entry?.readiness?.enabled && <button type="button" disabled={!ready || busy || confirmation} onClick={() => setConfirmation(true)} className="ml-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{t('businessScenarios.activation.enable')}</button>}
    {confirmation && <div role="alert" className="space-y-3 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm leading-6 dark:border-blue-800 dark:bg-blue-900/20"><p>{t('businessScenarios.activation.confirm', { name })}</p><p className="text-xs">{serving ? t('businessScenarios.activation.currentServing', { names: serving }) : t('businessScenarios.activation.noServing')}</p><div className="flex gap-3"><button ref={confirmButton} type="button" disabled={!ready || busy} onClick={() => void activate()} className="rounded-xl bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50">{t('businessScenarios.activation.confirmEnable')}</button><button type="button" onClick={() => setConfirmation(false)} className="rounded-xl border border-gray-200 px-4 py-2 dark:border-gray-700">{t('common.cancel')}</button></div></div>}
  </section>;
}
