import { useTranslation } from 'react-i18next';
import type { CreateAgentFormData } from '@/types';
import { useScenarioDraftStore } from '@/stores/scenarioDraftStore';
import AgentConfigurationGuide from './AgentConfigurationGuide';
import { matchesSavedAgent, useScenarioAgentStore } from '@/stores/scenarioAgentStore';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';
import { agentDraftInput } from '@/utils/agentScenarioForm';
import AgentTrialPanel from './AgentTrialPanel';
import AgentActivationPanel from './AgentActivationPanel';
import AgentBusinessConnections from './AgentBusinessConnections';

export default function AgentSetupReview({ form, testing, onActivated, onTrial }: {
  form: CreateAgentFormData; testing: boolean; onActivated?: () => void; onTrial?: () => void;
}) {
  const { t } = useTranslation();
  const template = useScenarioDraftStore(state => state.templates.find(value => value.module === 'agents' && value.id === form.scenarioId));
  const current = useScenarioDraftStore(state => state.current);
  const setup = useScenarioAgentStore();
  const channel = useScenarioResourceStore(state => state.channel);
  let ready = false;
  if (template && current && setup.receipt?.draft_id === current.id && setup.receipt.revision === current.revision) {
    try { ready = matchesSavedAgent(agentDraftInput(form, template, channel, 'testing', current), current); } catch { /* Unsaved tool credentials cannot be trialed. */ }
  }
  const prepare = async () => {
    if (!template) return;
    try { await setup.prepare(agentDraftInput(form, template, channel, 'testing', current)); }
    catch { useScenarioAgentStore.setState({ failed: true }); }
  };
  return <section className="space-y-5">
    <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t(`businessScenarios.wizard.${testing ? 'testTitle' : 'reviewTitle'}`)}</h3>
    {testing && <div className="rounded-3xl border border-gray-100 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
      {ready && setup.receipt ? <AgentTrialPanel key={setup.receipt.agent_id} agentId={setup.receipt.agent_id} questions={template?.test_questions} initialChannel={channel} /> : <>
        <p className="text-sm text-gray-600 dark:text-gray-300">{t('businessScenarios.wizard.saveBeforeTrial')}</p>
        <button type="button" disabled={setup.busy || !template} onClick={() => void prepare()} className="mt-3 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{t(`businessScenarios.wizard.${setup.busy ? 'savingEmployee' : 'saveAndTrial'}`)}</button>
      </>}
      {setup.failed && <p role="alert" className="mt-3 text-sm text-red-600">{t('businessScenarios.wizard.saveEmployeeFailed')}</p>}
    </div>}
    {!testing && <dl className="grid gap-4 rounded-3xl border border-gray-100 bg-white p-5 text-sm dark:border-gray-800 dark:bg-gray-900 sm:grid-cols-2">
      <div><dt className="text-gray-500">{t('agents.form.name')}</dt><dd className="mt-1 font-medium text-gray-800 dark:text-gray-100">{form.name}</dd></div>
      <div><dt className="text-gray-500">{t('agents.form.profession')}</dt><dd className="mt-1 font-medium text-gray-800 dark:text-gray-100">{form.profession}</dd></div>
      <div className="sm:col-span-2"><dt className="text-gray-500">{t('businessScenarios.wizard.company')}</dt><dd className="mt-1 whitespace-pre-wrap text-gray-700 dark:text-gray-200">{form.companySummary}</dd></div>
    </dl>}
    {!testing && ready && setup.receipt && <AgentActivationPanel key={setup.receipt.agent_id} agentId={setup.receipt.agent_id} name={form.name} onActivated={onActivated} onTrial={onTrial} />}
    {ready && setup.receipt && (form.scenarioId === 'logistics' || form.scenarioId === 'assistant' || Boolean(current?.bindings.connection_ids.length)) && <AgentBusinessConnections key={setup.receipt.agent_id} agentId={setup.receipt.agent_id} />}
    <AgentConfigurationGuide form={form} />
    <p className="rounded-2xl bg-blue-50 p-4 text-xs leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{t('businessScenarios.wizard.inactiveHint')}</p>
  </section>;
}
