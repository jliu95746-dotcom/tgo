import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Play } from 'lucide-react';
import { useAIStore } from '@/stores/aiStore';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { governedLogisticsWorkflowNodes, linearWorkflowNodes } from '@/utils/businessScenarios';
import { describeWorkflowReference } from '@/utils/workflowSetup';
import type { AgentNodeData, APINodeData, AnswerNodeData, InputNodeData } from '@/types/workflow';
import WorkflowAgentSelection from './WorkflowAgentSelection';
import WorkflowLogisticsBasicConfiguration from './WorkflowLogisticsBasicConfiguration';

export default function WorkflowBasicConfiguration({ onAdvanced }: { onAdvanced: () => void }) {
  const { t } = useTranslation();
  const { currentWorkflow, updateNode, validationErrors, isExecuting } = useWorkflowStore();
  const { agents, refreshAgents, isLoadingAgents, agentsError } = useAIStore();
  const [preparing, setPreparing] = useState(false);
  const [testError, setTestError] = useState('');
  const preparingRef = useRef(false);
  const needsAgents = currentWorkflow?.definition.nodes.some(node => node.type === 'agent') ?? false;
  useEffect(() => { if (needsAgents) void refreshAgents(); }, [refreshAgents, needsAgents]);
  if (!currentWorkflow) return null;
  const logisticsNodes = governedLogisticsWorkflowNodes(currentWorkflow.definition);
  const nodes = linearWorkflowNodes(currentWorkflow.definition);
  const missingAgent = currentWorkflow.definition.nodes.some(node => node.type === 'agent' && !agents.some(agent => agent.id === (node.data as AgentNodeData).agent_id));
  const prepareTest = async () => {
    if (preparingRef.current || isExecuting || (needsAgents && (isLoadingAgents || agentsError || missingAgent))) return;
    const owner = useAuthStore.getState().user;
    const stillCurrent = () => {
      const user = useAuthStore.getState().user;
      return owner && user?.id === owner.id && user?.project_id === owner.project_id && user?.role === owner.role
        && useWorkflowStore.getState().currentWorkflow?.id === currentWorkflow.id;
    };
    preparingRef.current = true; setPreparing(true); setTestError('');
    try {
      if (useWorkflowStore.getState().isDirty) await useWorkflowStore.getState().saveWorkflow();
      if (!stillCurrent()) return;
      if (useWorkflowStore.getState().isDirty) throw new Error(t('businessScenarios.workflow.changedDuringSave'));
      useWorkflowStore.getState().setSelectedNode(null);
      useWorkflowStore.getState().setDebugPanelOpen(true);
    } catch (error) {
      if (stillCurrent()) setTestError(error instanceof Error ? error.message : t('businessScenarios.workflow.testSaveFailed'));
    } finally { preparingRef.current = false; setPreparing(false); }
  };
  const businessReference = (expression: string | undefined) => {
    const reference = describeWorkflowReference(expression, currentWorkflow.definition.nodes);
    return reference ? t(`businessScenarios.workflow.references.${reference.kind}`, { label: reference.label }) : t('businessScenarios.workflow.references.custom');
  };
  const fieldClass = 'mt-2 w-full rounded-xl border border-gray-200 bg-white p-3 text-sm text-gray-900 outline-none focus:border-purple-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
  if (logisticsNodes) return <WorkflowLogisticsBasicConfiguration nodes={logisticsNodes} onAdvanced={onAdvanced} />;
  return <div className="h-full overflow-auto p-4 md:p-8">
    <div className="mx-auto max-w-3xl space-y-5">
      {validationErrors.length > 0 && <ul role="alert" className="space-y-2 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-900 dark:bg-red-900/20">{validationErrors.map((error, index) => <li key={index}>{error.message}</li>)}</ul>}
      <div className="rounded-2xl border border-purple-100 bg-purple-50 p-4 text-sm leading-6 text-purple-700 dark:border-purple-900 dark:bg-purple-900/20 dark:text-purple-300">
        <h2 className="font-bold">{t('businessScenarios.workflow.configurationTitle')}</h2>
        <p className="mt-1">{t('businessScenarios.workflow.configurationSteps')}</p>
        <p className="mt-2 text-xs">{t('businessScenarios.workflow.basicHelp')}</p>
      </div>
      {!nodes ? <div className="rounded-2xl bg-white p-6 dark:bg-gray-900"><p className="text-sm text-gray-500">{t('businessScenarios.workflow.unsupported')}</p><button onClick={onAdvanced} className="mt-4 rounded-xl bg-purple-600 px-4 py-2 text-sm text-white">{t('businessScenarios.workflow.advanced')}</button></div> : nodes.map((node, index) => <section key={node.id} className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="font-bold text-gray-900 dark:text-white">{index + 1}. {node.data.label}</h3>
        {node.type === 'input' && <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-300">
          <p className="text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.inputHelp')}</p>
          {(node.data as InputNodeData).input_variables?.map(variable => <div key={variable.name} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-gray-50 p-3 dark:bg-gray-800">
            <span>{variable.description || variable.name}</span><span className="text-xs text-gray-500">{t(`businessScenarios.workflow.inputs.types.${variable.type}`)} · {t(variable.required ? 'businessScenarios.workflow.inputs.requiredLabel' : 'businessScenarios.workflow.inputs.optionalLabel')}</span>
          </div>)}
        </div>}
        {node.type === 'agent' && <>
          <WorkflowAgentSelection value={(node.data as AgentNodeData).agent_id} disabled={preparing || isExecuting} onChange={id => updateNode(node.id, { agent_id: id, agent_name: agents.find(agent => agent.id === id)?.name })} />
          {Object.entries((node.data as AgentNodeData).input_mapping || {}).map(([field, expression]) => <p key={field} className="mt-3 text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.receives')}：{businessReference(expression)}</p>)}
        </>}
        {node.type === 'api' && <label className="mt-4 block text-sm text-gray-600 dark:text-gray-300">{t('businessScenarios.workflow.apiAddress')}<input type="url" value={(node.data as APINodeData).url} disabled={preparing || isExecuting} onChange={event => updateNode(node.id, { url: event.target.value })} className={fieldClass} /><span className="mt-2 block text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.apiMethod', { method: (node.data as APINodeData).method })} · {t('businessScenarios.workflow.apiHelp')}</span></label>}
        {node.type === 'answer' && <div className="mt-3 space-y-2 text-sm text-gray-600 dark:text-gray-300">
          <p>{t('businessScenarios.workflow.output')}：{(node.data as AnswerNodeData).output_type === 'variable' ? businessReference((node.data as AnswerNodeData).output_variable) : t('businessScenarios.workflow.references.custom')}</p>
          <p className="text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.outputHelp')}</p>
        </div>}
      </section>)}
      {nodes && <section className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="font-bold text-gray-900 dark:text-white">{t('businessScenarios.workflow.nextTest')}</h3>
        <p className="mt-2 text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.prepareTestHelp')}</p>
        {testError && <p role="alert" className="mt-3 break-words rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{testError}</p>}
        <button type="button" onClick={prepareTest} disabled={preparing || isExecuting || (needsAgents && (isLoadingAgents || Boolean(agentsError) || missingAgent))}
          className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-purple-600 px-4 py-3 text-sm font-medium text-white hover:bg-purple-700 disabled:opacity-50">
          {preparing ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}{t('businessScenarios.workflow.prepareTest')}
        </button>
      </section>}
    </div>
  </div>;
}
