import { useTranslation } from 'react-i18next';
import { useAgentActivationEntry } from '@/hooks/useAgentActivationEntry';
import { activationUsable } from '@/stores/agentActivationStore';

export default function AgentActivationSummary({ agentId, enabled, onOpen }: {
  agentId: string; enabled: boolean; onOpen: () => void;
}) {
  const { t } = useTranslation();
  const { entry, now, scope } = useAgentActivationEntry(agentId);
  if (!scope) return null;
  const ready = activationUsable(entry, now);
  const status = entry?.loading ? 'checking' : ready ? 'ready' : entry?.readinessReady ? 'blocked' : 'unavailable';
  return <section className="space-y-2 rounded-xl border border-gray-100 bg-gray-50/60 p-3 text-xs dark:border-gray-700 dark:bg-gray-900/20">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="font-medium text-gray-700 dark:text-gray-200">{t(`businessScenarios.activation.${enabled ? 'serving' : 'paused'}`)}</span>
      <span className={ready ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}>{t(`businessScenarios.activation.${status}`)}</span>
    </div>
    <button type="button" onClick={onOpen} className="font-medium text-blue-600 hover:underline dark:text-blue-300">{t('businessScenarios.activation.open')}</button>
  </section>;
}
