import { useTranslation } from 'react-i18next';
import { Check } from 'lucide-react';
import { AGENT_SETUP_STEPS } from '@/utils/agentScenarioForm';
import type { ScenarioStep } from '@/types/scenarioDrafts';

export default function AgentSetupNavigation({ step, disabled, onChange }: { step: ScenarioStep; disabled: boolean; onChange: (step: ScenarioStep) => void }) {
  const { t } = useTranslation();
  const active = AGENT_SETUP_STEPS.indexOf(step);
  return <nav aria-label={t('businessScenarios.wizard.progress')} className="border-b border-gray-100 bg-white px-4 py-4 dark:border-gray-800 dark:bg-gray-900 sm:px-8">
    <ol className="flex gap-2 overflow-x-auto pb-1">{AGENT_SETUP_STEPS.map((value, index) => <li key={value} className="min-w-max flex-1">
      <button type="button" disabled={disabled || index > active} onClick={() => onChange(value)} aria-current={value === step ? 'step' : undefined}
        className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed ${value === step ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' : 'text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800'}`}>
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${index <= active ? 'bg-blue-600 text-white' : 'bg-gray-100 dark:bg-gray-800'}`}>{index < active ? <Check size={13} /> : index + 1}</span>
        {t(`businessScenarios.wizard.steps.${value}`)}
      </button>
    </li>)}</ol>
  </nav>;
}
