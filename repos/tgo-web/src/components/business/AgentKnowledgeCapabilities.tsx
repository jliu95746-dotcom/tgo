import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCw } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useAgentKnowledgeCapabilityStore } from '@/stores/agentKnowledgeCapabilityStore';

export default function AgentKnowledgeCapabilities({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const store = useAgentKnowledgeCapabilityStore();
  const load = store.load;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (user?.role !== 'admin') return;
    void load(agentId);
    const focus = () => { setNow(Date.now()); void load(agentId, 'web', true); };
    window.addEventListener('focus', focus);
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => { window.removeEventListener('focus', focus); window.clearInterval(timer); };
  }, [agentId, user?.id, user?.project_id, user?.role, load]);
  if (user?.role !== 'admin') return null;
  const entry = store.identity === `${user.project_id}:${user.id}` ? store.entries[`${agentId}:web`] : undefined;
  return <section className="space-y-2 rounded-xl border border-gray-100 bg-gray-50/60 p-3 dark:border-gray-700 dark:bg-gray-900/20">
    <div className="flex items-center justify-between gap-2">
      <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-200">{t('businessScenarios.capability.title')}</h4>
      <button type="button" onClick={() => void load(agentId, 'web', true)} disabled={entry?.loading}
        aria-label={t('businessScenarios.capability.refresh')} className="rounded-lg p-1 text-gray-500 hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-700">
        <RotateCw size={13} className={entry?.loading ? 'animate-spin' : ''} />
      </button>
    </div>
    {(!entry || entry.loading) && <p className="text-xs text-gray-500" aria-live="polite">{t('businessScenarios.capability.loading')}</p>}
    {entry?.error && <p className="text-xs text-amber-700 dark:text-amber-200" role="alert">{t('businessScenarios.capability.error')}</p>}
    {entry?.data?.capabilities.map(group => {
      const effective = entry.ready && group.status === 'passed' && Boolean(group.valid_until && Date.parse(group.valid_until) > now);
      const status = !entry.ready ? 'unavailable' : group.status === 'passed' && !effective ? 'needs_retest' : group.status;
      return <div key={group.template_id} className="flex flex-wrap items-center justify-between gap-1 text-xs">
        <span className="text-gray-600 dark:text-gray-300">{t(`businessScenarios.capability.names.${group.template_id}`)}</span>
        <span className={effective ? 'text-green-700 dark:text-green-300' : 'text-gray-500 dark:text-gray-400'}>{t(`businessScenarios.capability.states.${status}`)}</span>
        {entry.ready && <p className="w-full text-[11px] leading-5 text-gray-500 dark:text-gray-400">{t('businessScenarios.capability.counts', {
          answered: group.cases.filter(row => row.case.expected_result === 'grounded_answer' && row.status === 'passed' && row.valid_until && Date.parse(row.valid_until) > now).length,
          answers: group.answer_cases_required,
          safe: group.cases.filter(row => row.case.expected_result === 'safe_refusal' && row.status === 'passed' && row.valid_until && Date.parse(row.valid_until) > now).length,
          safety: group.safety_cases_required,
        })}</p>}
      </div>;
    })}
    {entry?.ready && entry.data && <p className="text-[11px] text-gray-400 dark:text-gray-500">{t('businessScenarios.capability.checkedAt', {
      time: new Date(entry.data.checked_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }),
    })}</p>}
    <p className="text-[11px] leading-5 text-gray-400 dark:text-gray-500">{t('businessScenarios.capability.hint')}</p>
  </section>;
}
