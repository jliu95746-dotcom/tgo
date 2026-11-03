import { useTranslation } from 'react-i18next';
import { Briefcase, CheckCircle2 } from 'lucide-react';
import type { CreateAgentFormData } from '@/types';
import type { ScenarioTemplate } from '@/types/scenarioDrafts';
import { useScenarioDraftStore } from '@/stores/scenarioDraftStore';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';
import { initialRoleSettings } from '@/utils/agentScenarioForm';

export default function AgentRoleSelection({ form, onChange }: { form: CreateAgentFormData; onChange: (value: Partial<CreateAgentFormData>) => void }) {
  const { t } = useTranslation();
  const { templates, busy, failed, startNew, setStep } = useScenarioDraftStore();
  const choose = (template: ScenarioTemplate) => {
    if (form.scenarioId === template.id) { setStep('business'); return; }
    const previous = templates.find(value => value.module === 'agents' && value.id === form.scenarioId);
    startNew();
    onChange({
      scenarioId: template.id,
      ...initialRoleSettings(template.id, form),
      name: !form.name || form.name === previous?.business_defaults.name ? template.business_defaults.name : form.name,
      profession: !form.profession || form.profession === previous?.business_defaults.profession ? template.business_defaults.profession : form.profession,
      description: !form.description || form.description === previous?.business_defaults.instructions ? template.business_defaults.instructions : form.description,
      llmModel: form.llmModel || SYSTEM_DEFAULT_MODEL,
    });
    setStep('business');
  };
  return <section aria-label={t('businessScenarios.wizard.steps.scenario')}>
    <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.wizard.roleTitle')}</h3>
    <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">{t('businessScenarios.wizard.roleHint')}</p>
    {busy && <p role="status" className="mt-4 text-sm text-gray-500">{t('businessScenarios.drafts.loading')}</p>}
    {failed && <p role="alert" className="mt-4 text-sm text-red-600">{t('businessScenarios.drafts.failed')}</p>}
    <div className="mt-5 grid gap-4 sm:grid-cols-2">{templates.filter(value => value.module === 'agents').map(template => <button key={template.id} type="button" disabled={busy || !template.available} onClick={() => choose(template)}
      className={`rounded-3xl border bg-white p-5 text-left shadow-sm transition-colors disabled:opacity-50 dark:bg-gray-900 ${form.scenarioId === template.id ? 'border-blue-500 ring-2 ring-blue-500/10' : 'border-gray-100 hover:border-blue-300 dark:border-gray-800'}`}>
      <span className="flex items-center justify-between text-blue-600 dark:text-blue-400"><Briefcase size={21} />{form.scenarioId === template.id && <CheckCircle2 size={19} />}</span>
      <h4 className="mt-3 font-bold text-gray-900 dark:text-gray-100">{t(`businessScenarios.scenes.${template.id}.title`)}</h4>
      <p className="mt-2 text-xs leading-6 text-gray-500 dark:text-gray-400">{t(`businessScenarios.scenes.${template.id}.benefit`)}</p>
      <p className="mt-3 text-xs text-blue-600 dark:text-blue-300">{t('businessScenarios.requirements')}：{template.required_resources.map(kind => t(`businessScenarios.preparation.kinds.${kind}`)).join('、')}</p>
    </button>)}</div>
  </section>;
}
