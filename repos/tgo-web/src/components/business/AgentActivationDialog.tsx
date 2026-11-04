import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { useAuthStore } from '@/stores/authStore';
import type { Agent } from '@/types';
import AgentActivationPanel from './AgentActivationPanel';

export default function AgentActivationDialog({ agent, onClose, onTrial }: { agent: Agent; onClose: () => void; onTrial: () => void }) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const enabled = user?.role === 'admin';
  const dialog = useDialogFocus<HTMLElement>(onClose, enabled);
  if (!enabled) return null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 sm:p-6" onClick={onClose}>
    <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="agent-activation-title" className="flex max-h-[90dvh] w-full max-w-3xl flex-col rounded-3xl bg-white shadow-xl dark:bg-gray-900" onClick={event => event.stopPropagation()}>
      <header className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800"><h2 id="agent-activation-title" className="break-words text-lg font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.activation.employeeTitle', { name: agent.name })}</h2><button type="button" aria-label={t('common.close')} onClick={onClose} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><X size={20} /></button></header>
      <div className="overflow-y-auto p-4 sm:p-6"><AgentActivationPanel agentId={agent.id} name={agent.name} onTrial={onTrial} /></div>
    </section>
  </div>;
}
