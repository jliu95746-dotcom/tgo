import { useEffect, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, FolderOpen, GitBranch, Monitor, Wrench } from 'lucide-react';
import type { AiTool, CreateAgentFormData, FormValidationErrors, KnowledgeBaseItem } from '@/types';
import type { Device } from '@/types/deviceControl';
import type { WorkflowSummary } from '@/types/workflow';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';
import { shouldExpandAgentResources } from '@/utils/agentSetupExperience';
import { useKnowledgeStore } from '@/stores/knowledgeStore';
import AgentResourceSettings from '@/components/ai/AgentResourceSettings';
import AgentKnowledgeBasesSection from '@/components/ui/AgentKnowledgeBasesSection';
import AgentToolsSection from '@/components/ui/AgentToolsSection';
import AgentWorkflowsSection from '@/components/ui/AgentWorkflowsSection';
import AgentDeviceSection from '@/components/ui/AgentDeviceSection';
import SectionHeader from '@/components/ui/SectionHeader';

type ResourceKind = 'knowledge' | 'tools' | 'workflows' | 'device';
interface Props {
  form: CreateAgentFormData;
  errors: FormValidationErrors;
  disabled: boolean;
  onChange: (value: Partial<CreateAgentFormData>) => void;
  llmOptions: Array<{ value: string; label: string }>;
  llmLoading: boolean;
  llmError: string | null;
  llmSelectRef: RefObject<HTMLSelectElement | null>;
  knowledgeBases: KnowledgeBaseItem[];
  tools: AiTool[];
  workflows: WorkflowSummary[];
  device: Device | null;
  onAdd: (kind: ResourceKind) => void;
  onRemoveKnowledge: (id: string) => void;
  onRemoveTool: (id: string) => void;
  onRemoveWorkflow: (id: string) => void;
  onRemoveDevice: () => void;
}

export default function AgentSetupResources(props: Props) {
  const { t } = useTranslation();
  const { form, disabled } = props;
  const { fetchKnowledgeBases, isLoading, error } = useKnowledgeStore();
  useEffect(() => { if (form.knowledgeBases.length > 0) void fetchKnowledgeBases(); }, [fetchKnowledgeBases, form.knowledgeBases.length]);
  const expand = shouldExpandAgentResources(form);
  const [expanded, setExpanded] = useState(expand);
  useEffect(() => { if (expand) setExpanded(true); }, [expand]);
  const card = 'rounded-3xl border border-gray-100 bg-white p-4 shadow-sm dark:border-gray-800 dark:bg-gray-900 sm:p-6';
  return <section className="space-y-5">
    <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-900/20">
      <h3 className="font-bold text-blue-700 dark:text-blue-300">{t('businessScenarios.wizard.knowledgeFirst')}</h3>
      <p className="mt-2 text-xs leading-6 text-gray-600 dark:text-gray-300">{t('businessScenarios.wizard.knowledgeHint')}</p>
      <p className="mt-2 text-xs text-blue-700 dark:text-blue-300">{t(form.llmModel === SYSTEM_DEFAULT_MODEL ? 'businessScenarios.wizard.modelFollowing' : 'businessScenarios.wizard.modelSelected')}</p>
    </div>
    <div className={card}>
      <SectionHeader icon={<FolderOpen className="h-4 w-4 text-teal-600" />} title={t('agents.form.knowledge_bases')} />
      <AgentKnowledgeBasesSection items={props.knowledgeBases} unresolvedCount={Math.max(0, form.knowledgeBases.length - props.knowledgeBases.length)} disabled={disabled} onAdd={() => props.onAdd('knowledge')} onRemove={props.onRemoveKnowledge} />
      {isLoading && <p role="status" className="mt-3 text-xs text-gray-500">{t('businessScenarios.wizard.knowledgeLoading')}</p>}
      {error && <div role="alert" className="mt-3 space-y-2 text-xs text-red-600 dark:text-red-400"><p>{t('businessScenarios.wizard.knowledgeLoadFailed')}</p><button type="button" disabled={disabled || isLoading} onClick={() => void fetchKnowledgeBases()} className="font-medium underline">{t('common.retry', '重试')}</button></div>}
    </div>
    <section>
      <button type="button" aria-expanded={expanded} disabled={disabled} onClick={() => setExpanded(!expanded)} className={`${card} flex w-full items-center justify-between gap-3 text-left`}>
        <span><span className="block text-sm font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.wizard.additionalResources')}</span>
          <span className="mt-2 block text-xs leading-5 text-gray-500 dark:text-gray-400">{t('businessScenarios.wizard.additionalHint')}</span></span>
        <ChevronDown size={20} className={`shrink-0 text-gray-500 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && <div className="mt-5 space-y-5">
        <AgentResourceSettings form={form} errors={props.errors} disabled={disabled} onChange={props.onChange} llmOptions={props.llmOptions} llmLoading={props.llmLoading} llmError={props.llmError} llmSelectRef={props.llmSelectRef} />
        <div className={card}>
          <SectionHeader icon={<Wrench className="h-4 w-4 text-orange-600" />} title={t('agents.create.sections.tools')} />
          <AgentToolsSection tools={props.tools} toolConfigs={form.toolConfigs} disabled={disabled} onAdd={() => props.onAdd('tools')} onRemove={props.onRemoveTool} />
        </div>
        <div className={card}>
          <SectionHeader icon={<GitBranch className="h-4 w-4 text-purple-600" />} title={t('workflow.title')} />
          <AgentWorkflowsSection workflows={props.workflows} disabled={disabled} onAdd={() => props.onAdd('workflows')} onRemove={props.onRemoveWorkflow} />
        </div>
        <div className={card}>
          <SectionHeader icon={<Monitor className="h-4 w-4 text-purple-600" />} title={t('agents.deviceSection.title')} />
          <AgentDeviceSection device={props.device} disabled={disabled} onAdd={() => props.onAdd('device')} onRemove={props.onRemoveDevice} />
        </div>
      </div>}
    </section>
  </section>;
}
