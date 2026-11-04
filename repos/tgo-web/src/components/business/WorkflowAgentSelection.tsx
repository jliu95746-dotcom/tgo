import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useAIStore } from '@/stores/aiStore';

interface WorkflowAgentSelectionProps {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  prefilled?: boolean;
}

export default function WorkflowAgentSelection({ value, onChange, disabled = false, prefilled = false }: WorkflowAgentSelectionProps) {
  const { t } = useTranslation();
  const { agents, agentsError, isLoadingAgents, refreshAgents } = useAIStore();
  const selected = agents.find(agent => agent.id === value);
  return <div className="mt-4 space-y-3">
    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">
      {t('businessScenarios.workflow.agent')}
      <select required value={value} onChange={event => onChange(event.target.value)} disabled={disabled || isLoadingAgents || Boolean(agentsError)}
        className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-800 dark:text-white">
        <option value="">{t('businessScenarios.workflow.select')}</option>
        {value && !selected && <option value={value}>{t('businessScenarios.workflow.agentUnavailable')}</option>}
        {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
      </select>
    </label>
    {isLoadingAgents ? <p role="status" className="flex items-center gap-2 text-xs text-gray-500"><Loader2 size={14} className="animate-spin" />{t('businessScenarios.workflow.loadingAgents')}</p> : agentsError ?
      <div role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">
        <p>{t('businessScenarios.workflow.agentLoadFailed')}</p><p className="mt-1 break-words text-xs">{agentsError}</p>
        <button type="button" disabled={disabled} onClick={() => void refreshAgents()} className="mt-2 font-medium underline">{t('businessScenarios.workflow.retryAgents')}</button>
      </div> : <>
        {prefilled && selected && <p className="text-xs leading-5 text-purple-600 dark:text-purple-300">{t('businessScenarios.workflow.prefilledAgent')}</p>}
        {selected ? <div className="rounded-xl bg-gray-50 p-3 text-xs leading-6 text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          <p>{t(selected.llmModel === '__system_default__' ? 'businessScenarios.workflow.systemModel' : selected.llmModel ? 'businessScenarios.workflow.selectedModel' : 'businessScenarios.workflow.missingModel')}</p>
          <p>{t('businessScenarios.workflow.linkedKnowledge', { count: selected.knowledgeBases.length })}</p>
          {selected.status !== 'active' && <p className="text-amber-600 dark:text-amber-400">{t('businessScenarios.workflow.inactiveAgent')}</p>}
        </div> : <p className="text-xs leading-5 text-gray-500">{t(value ? 'businessScenarios.workflow.agentUnavailable' : agents.length ? 'businessScenarios.workflow.chooseAgent' : 'businessScenarios.workflow.noAgents')}</p>}
        <p className="text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.agentHelp')}</p>
        <div className="flex flex-wrap items-center gap-4 text-xs font-medium text-purple-600 dark:text-purple-300">
          <Link to="/ai/agents" target="_blank" rel="noopener noreferrer">{t('businessScenarios.workflow.configureAgent')}</Link>
          <button type="button" disabled={disabled} onClick={() => void refreshAgents()}>{t('businessScenarios.workflow.retryAgents')}</button>
        </div>
      </>}
  </div>;
}
