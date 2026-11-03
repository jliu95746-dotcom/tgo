import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { useAuthStore } from '@/stores/authStore';
import type { Agent } from '@/types';
import AgentTrialPanel from './AgentTrialPanel';

interface Props { agent: Agent | null; onClose: () => void }

export default function AgentTrialDialog({ agent, onClose }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const enabled = Boolean(agent && user?.role === 'admin');
  const dialog = useDialogFocus<HTMLElement>(onClose, enabled);
  if (!agent || !enabled) return null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 sm:p-6" onClick={onClose}>
    <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="agent-trial-title" className="flex max-h-[90dvh] w-full max-w-3xl flex-col rounded-3xl bg-white shadow-xl dark:bg-gray-900" onClick={event => event.stopPropagation()}>
      <header className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800 sm:px-6"><h3 id="agent-trial-title" className="min-w-0 break-words text-lg font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.trial.title', { name: agent.name })}</h3><button type="button" onClick={onClose} aria-label={t('businessScenarios.trial.close')} className="shrink-0 rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><X size={20} /></button></header>
      <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-6"><AgentTrialPanel key={`${user?.project_id}:${user?.id}:${agent.id}`} agentId={agent.id} collections={agent.collections} /></div>
      <footer className="border-t border-gray-100 px-5 py-3 text-xs leading-5 text-gray-500 dark:border-gray-800 dark:text-gray-400 sm:px-6">{t('businessScenarios.trial.closeHint')}</footer>
    </section>
  </div>;
}
