import { useTranslation } from 'react-i18next';
import { CheckCircle2, Circle, Lightbulb } from 'lucide-react';
import type { CreateAgentFormData } from '@/types';
import { BUSINESS_SCENARIOS } from '@/utils/businessScenarios';
import ScenarioReadinessCheck from './ScenarioReadinessCheck';

export default function AgentConfigurationGuide({ form }: { form: CreateAgentFormData }) {
  const { t } = useTranslation();
  const scenario = BUSINESS_SCENARIOS.find(item => item.module === 'agents' && item.id === form.scenarioId);
  const needsKnowledge = form.scenarioId === 'knowledge' || form.scenarioId === 'aftersales';
  const checks = [
    { key: 'knowledge', count: form.knowledgeBases.length },
    { key: 'tools', count: form.tools.length },
    { key: 'workflow', count: form.workflows.length },
    { key: 'device', count: form.boundDeviceId ? 1 : 0 },
  ].filter(check => check.count > 0 || (check.key === 'knowledge' && needsKnowledge));
  return <section className="mb-6 rounded-2xl border border-blue-100 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-900/10">
    <h3 className="flex items-center gap-2 text-sm font-bold text-blue-700 dark:text-blue-300"><Lightbulb size={16} />{t('businessScenarios.agentGuide.title')}</h3>
    <p className="mt-2 text-xs leading-6 text-gray-600 dark:text-gray-300">{t('businessScenarios.agentGuide.text')}</p>
    {scenario && <p className="mt-2 text-xs leading-6 text-blue-700 dark:text-blue-300">{t('businessScenarios.requirements')}：{scenario.requirements.map(key => t(`businessScenarios.resources.${key}`)).join('、')}</p>}
    <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">{checks.map(check => <div key={check.key} className="rounded-xl bg-white/70 p-2 text-xs dark:bg-gray-800">
      <div className="flex items-center gap-1 text-gray-700 dark:text-gray-200">{check.count ? <CheckCircle2 size={14} className="text-green-500" /> : <Circle size={14} className="text-gray-400" />}{t(`businessScenarios.agentGuide.${check.key}`)}</div>
      <p className="mt-1 text-gray-500">{check.count ? t('businessScenarios.agentGuide.selected', { count: check.count }) : t('businessScenarios.agentGuide.missing')}</p>
    </div>)}</div>
    <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.agentGuide.test')}</p>
    <ScenarioReadinessCheck form={form} />
  </section>;
}
