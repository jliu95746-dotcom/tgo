import React, { useState, useMemo, useEffect, useRef } from 'react';
import { X, Save, RotateCcw, Bot, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useAIStore, useKnowledgeStore } from '@/stores';
import { useProjectToolsStore } from '@/stores/projectToolsStore';
import { useDeviceControlStore } from '@/stores/deviceControlStore';

import { useToast } from '@/hooks/useToast';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { transformAiToolResponseList } from '@/utils/projectToolsTransform';
// import { generateDefaultAvatar } from '@/utils/avatarUtils';

import ToolSelectionModal from './ToolSelectionModal';
import KnowledgeBaseSelectionModal from './KnowledgeBaseSelectionModal';
import DeviceSelectionModal from './DeviceSelectionModal';
import { WorkflowSelectionModal } from '@/components/workflow';
import type { AiTool } from '@/types';
import type { WorkflowSummary } from '@/types/workflow';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useAgentForm } from '@/hooks/useAgentForm';
import AgentBusinessFields from './AgentBusinessFields';
import AgentSetupResources from '@/components/business/AgentSetupResources';
import AgentSetupNavigation from '@/components/business/AgentSetupNavigation';
import AgentRoleSelection from '@/components/business/AgentRoleSelection';
import AgentSetupReview from '@/components/business/AgentSetupReview';
import { useAgentActivationStore } from '@/stores/agentActivationStore';
import { useScenarioDraftStore } from '@/stores/scenarioDraftStore';
import { useAuthStore } from '@/stores/authStore';
import { saveAgentSetupDraft } from '@/utils/agentSetupExperience';
import { AGENT_SETUP_STEPS, agentDraftInput, agentStepProblem } from '@/utils/agentScenarioForm';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';
import ScenarioDraftControls from '@/components/business/ScenarioDraftControls';
import AIProvidersApiService from '@/services/aiProvidersApi';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';
import { useScenarioAgentStore } from '@/stores/scenarioAgentStore';
import { useAgentTrialStore } from '@/stores/agentTrialStore';


/**
 * AI员工创建模态框组件
 */
const CreateAgentModal: React.FC = () => {
  const {
    showCreateAgentModal,
    createAgentFormData,
    createAgentErrors,
    isCreatingAgent,

    setShowCreateAgentModal,
    setCreateAgentFormData,
    resetCreateAgentForm,
    refreshAgents
  } = useAIStore();

  const { knowledgeBases } = useKnowledgeStore();
  const { aiTools } = useProjectToolsStore();
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { step, setStep, current, templates, busy: draftBusy, startNew } = useScenarioDraftStore();
  const channel = useScenarioResourceStore(state => state.channel);
  const setup = useScenarioAgentStore();
  const activationBusy = useAgentActivationStore(state => Boolean(setup.receipt && (state.entries[setup.receipt.agent_id]?.enabling || state.entries[setup.receipt.agent_id]?.writing)));
  const isSetupBusy = isCreatingAgent || draftBusy || setup.busy || activationBusy;
  const isTrialPending = useAgentTrialStore(state => Boolean(state.pending) && state.agentId === setup.receipt?.agent_id);
  const [confirmClose, setConfirmClose] = useState(false);
  const [closeError, setCloseError] = useState('');
  const currentUser = useAuthStore(state => state.user);
  const stepIndex = AGENT_SETUP_STEPS.indexOf(step);
  const nextStep = () => {
    const problem = agentStepProblem(step, createAgentFormData);
    if (problem) { showToast('error', t('agents.messages.validationFailed'), t(`businessScenarios.wizard.${problem}`)); return; }
    setStep(AGENT_SETUP_STEPS[Math.min(stepIndex + 1, AGENT_SETUP_STEPS.length - 1)]);
  };

  // Refs for auto-scrolling to the first invalid field on validation failure
  const nameInputRef = useRef<HTMLInputElement | null>(null);
  const professionInputRef = useRef<HTMLInputElement | null>(null);
  const llmSelectRef = useRef<HTMLSelectElement | null>(null);
  const descriptionTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  const [llmOptions, setLlmOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);


  // Fetch chat models from /v1/ai-models with model_type=chat
  useEffect(() => {
    if (!showCreateAgentModal) return;
    let cancelled = false;
    const fetchChatOptions = async () => {
      setLlmLoading(true);
      setLlmError(null);
      try {
        const svc = new AIProvidersApiService();
        const res = await svc.listProjectModels({ model_type: 'chat', is_active: true });
        const options = (res.data || []).map(m => ({
          value: `${m.provider_id}:${m.model_id}`,
          label: `${m.model_name} · ${m.provider_name}`,
        }));
        if (!cancelled) {
          setLlmOptions(options);
        }
      } catch (e) {
        if (!cancelled) setLlmError(e instanceof Error ? e.message : t('agents.create.models.error', '加载模型失败'));
      } finally {
        if (!cancelled) setLlmLoading(false);
      }
    };
    fetchChatOptions();
    return () => { cancelled = true; };
  }, [showCreateAgentModal, t]);

  // Following is a persistent mode, not a copy of today's default model.
  useEffect(() => {
    if (showCreateAgentModal && !createAgentFormData.llmModel) setCreateAgentFormData({ llmModel: SYSTEM_DEFAULT_MODEL });
  }, [showCreateAgentModal, createAgentFormData.llmModel, setCreateAgentFormData]);

  // Tool selection modal state
  const [showToolSelectionModal, setShowToolSelectionModal] = useState(false);

  // Knowledge base selection modal state
  const [showKnowledgeBaseSelectionModal, setShowKnowledgeBaseSelectionModal] = useState(false);

  // Workflow selection modal state
  const [showWorkflowSelectionModal, setShowWorkflowSelectionModal] = useState(false);

  // Device selection modal state
  const [showDeviceSelectionModal, setShowDeviceSelectionModal] = useState(false);


  // Workflow store
  const { workflows, loadWorkflows } = useWorkflowStore();

  // Device store
  const { devices, loadDevices } = useDeviceControlStore();

  // Load devices when modal is open (for bound device display)
  useEffect(() => {
    if (showCreateAgentModal && devices.length === 0) {
      loadDevices().catch(() => {});
    }
  }, [showCreateAgentModal, devices.length, loadDevices]);

  // Load workflows when modal is open
  useEffect(() => {
    if (showCreateAgentModal && workflows.length === 0) {
      loadWorkflows().catch(() => {});
    }
  }, [showCreateAgentModal, workflows.length, loadWorkflows]);

  // Shared form logic (controlled by store formData)
  const {
    removeTool,
    removeKnowledgeBase,
    removeDevice,
    removeWorkflow,
  } = useAgentForm({
    controlledFormData: createAgentFormData,
    onFormDataChange: setCreateAgentFormData,
  });

  // Compute bound device object for display
  const boundDevice = useMemo(() => {
    if (!createAgentFormData.boundDeviceId) return null;
    return devices.find(d => d.id === createAgentFormData.boundDeviceId) || null;
  }, [devices, createAgentFormData.boundDeviceId]);







  // 已添加的Tool工具列表 - 显示所有已添加的工具（包括启用和禁用的）
  const addedAiTools = useMemo(() => {
    // Transform AI tools (from NEW /v1/ai/tools API) to AiTool format
    const tools = transformAiToolResponseList(aiTools);
    return tools.filter((tool: AiTool) => createAgentFormData.tools.includes(tool.id));
  }, [aiTools, createAgentFormData.tools]);

  // 知识库启用状态：后端暂不支持单独启用/禁用，选中即关联

  // 已添加的知识库列表 - 显示所有已添加的知识库（包括启用和禁用的）
  const addedKnowledgeBases = useMemo(() => {
    return knowledgeBases.filter(kb => createAgentFormData.knowledgeBases.includes(kb.id));
  }, [knowledgeBases, createAgentFormData.knowledgeBases]);

  // 已添加的工作流列表
  const addedWorkflows = useMemo<WorkflowSummary[]>(() => {
    return workflows.filter(wf => createAgentFormData.workflows.includes(wf.id));
  }, [workflows, createAgentFormData.workflows]);

  const prepareForTrial = async () => {
    for (const previousStep of AGENT_SETUP_STEPS.slice(0, 3)) {
      const problem = agentStepProblem(previousStep, createAgentFormData);
      if (problem) {
        setStep(previousStep);
        showToast('error', t('agents.messages.validationFailed'), t(`businessScenarios.wizard.${problem}`));
        return null;
      }
    }
    const template = templates.find(value => value.module === 'agents' && value.id === createAgentFormData.scenarioId);
    if (!template) return null;
    try {
      return await setup.prepare(agentDraftInput(createAgentFormData, template, channel, 'testing', current));
    } catch (error) {
      showToast('error', t('agents.messages.validationFailed'), t(error instanceof Error && error.message === 'toolConfig'
        ? 'businessScenarios.drafts.toolConfig' : 'businessScenarios.wizard.saveEmployeeFailed'));
      return null;
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (isSetupBusy || isTrialPending || Object.values(useAgentActivationStore.getState().entries).some(entry => entry.enabling || entry.writing)) return;
    if (step === 'scenario' || step === 'business') { nextStep(); return; }
    const receipt = await prepareForTrial();
    if (!receipt) return;
    if (step === 'resources') { setStep('testing'); return; }
    if (step === 'testing') { setStep('activation'); return; }
    const employeeName = createAgentFormData.name;
    discardAndClose();
    showToast('success', t('agents.messages.createSuccess'), t('agents.messages.createSuccessDesc', { name: employeeName }));
    // A refresh failure must not cause a second creation of the saved employee.
    try { await refreshAgents(); } catch { /* Existing list error state provides retry. */ }
  };

  const discardAndClose = () => {
    setup.cancel();
    setConfirmClose(false);
    setShowCreateAgentModal(false);
    resetCreateAgentForm();
    startNew();
  };
  const handleClose = () => {
    if (isSetupBusy) return;
    setCloseError('');
    const template = templates.find(value => value.module === 'agents' && value.id === createAgentFormData.scenarioId);
    if (template && current?.template_id === template.id) {
      try {
        const input = agentDraftInput(createAgentFormData, template, channel, step, current);
        if (JSON.stringify([input.business, input.bindings, input.agent_settings, input.step, input.channel]) === JSON.stringify([current.business, current.bindings, current.agent_settings, current.step, current.channel])) {
          discardAndClose(); return;
        }
      } catch { /* Unsaved tool parameters require an explicit discard decision. */ }
    }
    if (createAgentFormData.scenarioId || createAgentFormData.name || createAgentFormData.description || createAgentFormData.companySummary) setConfirmClose(true);
    else discardAndClose();
  };
  const saveAndClose = async () => {
    if (isSetupBusy || isTrialPending) return;
    setCloseError('');
    try {
      if (await saveAgentSetupDraft(createAgentFormData)) discardAndClose();
      else setCloseError(t('businessScenarios.drafts.failed'));
    } catch (error) {
      setCloseError(t(error instanceof Error && error.message === 'toolConfig' ? 'businessScenarios.drafts.toolConfig' : 'businessScenarios.readiness.invalidModel'));
    }
  };

  const dialogRef = useDialogFocus<HTMLDivElement>(handleClose, showCreateAgentModal);
  const closeDialogRef = useDialogFocus<HTMLDivElement>(() => {
    if (!isSetupBusy && !isTrialPending) setConfirmClose(false);
  }, showCreateAgentModal && confirmClose);

  // 处理工具移除
  const handleToolRemove = (toolId: string) => {
    removeTool(toolId);
  };

  // 处理知识库移除
  const handleKnowledgeBaseRemove = (kbId: string) => {
    removeKnowledgeBase(kbId);
  };

  // 处理工作流移除
  const handleWorkflowRemove = (workflowId: string) => {
    removeWorkflow(workflowId);
  };

  // 知识库图标颜色由通用组件处理

  if (!showCreateAgentModal) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden flex items-center justify-center p-4 sm:p-6">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-gray-900/60 dark:bg-black/80 backdrop-blur-sm transition-opacity animate-in fade-in duration-300"
        onClick={handleClose}
      />

      {/* Modal Container */}
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="create-agent-title" className="relative bg-white dark:bg-gray-900 rounded-[2rem] shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col animate-in zoom-in-95 duration-300">
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-6 border-b border-gray-100 dark:border-gray-800 bg-gradient-to-r from-blue-600 to-indigo-700 text-white">
          <div className="flex items-center gap-4">
            <div className="p-3 bg-white/10 backdrop-blur-md rounded-2xl">
              <Bot className="w-7 h-7 text-white" />
            </div>
            <div>
              <h2 id="create-agent-title" className="text-xl font-bold">{t('agents.modal.create.title', '创建AI员工')}</h2>
              <p className="text-blue-100 text-xs mt-0.5 opacity-80">{t('agents.modal.create.subtitle', '定义一个具有专业技能的虚拟数字员工')}</p>
            </div>
          </div>
          <button
            onClick={handleClose}
            aria-label={t('businessScenarios.wizard.closeWindow')}
            className="p-2 text-white/70 hover:text-white hover:bg-white/10 rounded-xl transition-all"
            disabled={isSetupBusy}
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        <AgentSetupNavigation step={step} disabled={isSetupBusy || isTrialPending} onChange={setStep} />
        {/* Content */}
        <form onSubmit={handleSubmit} className="flex-1 flex flex-col min-h-0">
          <div className="flex-1 overflow-y-auto custom-scrollbar bg-gray-50 dark:bg-gray-950">
            <div className="p-4 space-y-6 sm:p-8 sm:space-y-8">
              <ScenarioDraftControls form={createAgentFormData} onResume={setCreateAgentFormData} />
              {step === 'scenario' && <AgentRoleSelection form={createAgentFormData} onChange={setCreateAgentFormData} />}
              {step === 'business' && <AgentBusinessFields form={createAgentFormData} errors={createAgentErrors} disabled={isSetupBusy} onChange={setCreateAgentFormData} nameInputRef={nameInputRef} professionInputRef={professionInputRef} descriptionTextareaRef={descriptionTextareaRef} />}
              {step === 'resources' && <AgentSetupResources
                form={createAgentFormData} errors={createAgentErrors} disabled={isSetupBusy} onChange={setCreateAgentFormData}
                llmOptions={llmOptions} llmLoading={llmLoading} llmError={llmError} llmSelectRef={llmSelectRef}
                knowledgeBases={addedKnowledgeBases} tools={addedAiTools} workflows={addedWorkflows} device={boundDevice}
                onAdd={kind => {
                  if (kind === 'knowledge') setShowKnowledgeBaseSelectionModal(true);
                  else if (kind === 'tools') setShowToolSelectionModal(true);
                  else if (kind === 'workflows') setShowWorkflowSelectionModal(true);
                  else setShowDeviceSelectionModal(true);
                }}
                onRemoveKnowledge={handleKnowledgeBaseRemove} onRemoveTool={handleToolRemove} onRemoveWorkflow={handleWorkflowRemove} onRemoveDevice={removeDevice}
              />}
              {(step === 'testing' || step === 'activation') && <AgentSetupReview form={createAgentFormData} testing={step === 'testing'} onActivated={discardAndClose} onTrial={() => setStep('testing')} />}
            </div>
          </div>

          {/* Footer */}
          {setup.failed && step !== 'testing' && <p role="alert" className="px-4 py-3 text-sm text-red-600 sm:px-8">{t('businessScenarios.wizard.saveEmployeeFailed')}</p>}
          <div className="px-4 sm:px-8 py-4 border-t border-gray-100 dark:border-gray-800 bg-white/50 dark:bg-gray-900/50 backdrop-blur-md flex flex-wrap items-center justify-end gap-2 rounded-b-[2rem]">
            <button
              type="button"
              onClick={handleClose}
              className="flex items-center px-6 py-2.5 text-sm font-bold text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-all"
              disabled={isSetupBusy}
            >
              {t('common.cancel', '取消')}
            </button>
            <button
              type="button"
              onClick={() => { resetCreateAgentForm(); startNew(); }}
              className="flex items-center gap-2 px-6 py-2.5 text-sm font-bold text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-all"
              disabled={isSetupBusy || isTrialPending}
            >
              <RotateCcw className="w-4 h-4" />
              {t('common.reset', '重置')}
            </button>
            {stepIndex > 0 && <button type="button" disabled={isSetupBusy || isTrialPending} onClick={() => setStep(AGENT_SETUP_STEPS[stepIndex - 1])} className="rounded-xl px-4 py-2.5 text-sm font-bold text-blue-600 dark:text-blue-300">{t('businessScenarios.wizard.previous')}</button>}
            <button
              type="submit"
              disabled={isSetupBusy || isTrialPending}
              className="flex items-center gap-2 px-8 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold rounded-xl shadow-lg shadow-blue-200 dark:shadow-none transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isCreatingAgent ? (
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                step === 'activation' ? <Save className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />
              )}
              {t(`businessScenarios.wizard.${step === 'activation' ? 'finishSetup' : setup.busy ? 'savingEmployee' : step === 'resources' ? 'saveAndTrial' : step === 'testing' ? 'nextReview' : 'next'}`)}
            </button>
          </div>
        </form>
      </div>

      {confirmClose && <div ref={closeDialogRef} role="alertdialog" aria-modal="true" aria-labelledby="agent-close-title" className="absolute inset-0 z-50 flex items-center justify-center bg-gray-900/50 p-4">
        <div className="w-full max-w-md rounded-3xl bg-white p-6 shadow-xl dark:bg-gray-900">
          <h3 id="agent-close-title" className="font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.wizard.closeTitle')}</h3>
          <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">{t('businessScenarios.wizard.closeHint')}</p>
          {closeError && <p role="alert" className="mt-3 text-xs leading-6 text-red-600 dark:text-red-400">{closeError}</p>}
          <div className="mt-5 flex flex-wrap justify-end gap-3">
            <button type="button" disabled={isSetupBusy || isTrialPending} onClick={discardAndClose} className="rounded-xl px-3 py-2 text-xs text-red-600 disabled:opacity-50">{t('businessScenarios.wizard.closeDiscard')}</button>
            <button type="button" disabled={isSetupBusy || isTrialPending} data-dialog-initial-focus onClick={() => setConfirmClose(false)} className="rounded-xl px-4 py-2 text-sm text-blue-600 disabled:opacity-50 dark:text-blue-300">{t('businessScenarios.wizard.closeKeep')}</button>
            <button type="button" disabled={isSetupBusy || isTrialPending || currentUser?.role !== 'admin' || !templates.some(template => template.module === 'agents' && template.id === createAgentFormData.scenarioId)} onClick={() => void saveAndClose()} className="rounded-xl bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t(isSetupBusy ? 'businessScenarios.drafts.loading' : 'businessScenarios.wizard.saveClose')}</button>
          </div>
        </div>
      </div>}

      {/* Tool Tool Selection Modal */}
      <ToolSelectionModal
        isOpen={showToolSelectionModal}
        onClose={() => setShowToolSelectionModal(false)}
        selectedTools={createAgentFormData.tools}
        toolConfigs={createAgentFormData.toolConfigs}
        onConfirm={(selectedToolIds, toolConfigs) => {
          setCreateAgentFormData({
            tools: selectedToolIds,
            toolConfigs
          });
          setShowToolSelectionModal(false);
        }}
      />


      {/* Knowledge Base Selection Modal */}
      <KnowledgeBaseSelectionModal
        isOpen={showKnowledgeBaseSelectionModal}
        onClose={() => setShowKnowledgeBaseSelectionModal(false)}
        selectedKnowledgeBases={createAgentFormData.knowledgeBases}
        onConfirm={(selectedKnowledgeBaseIds) => {
          setCreateAgentFormData({ knowledgeBases: selectedKnowledgeBaseIds });
        }}
      />

      {/* Workflow Selection Modal */}
      <WorkflowSelectionModal
        isOpen={showWorkflowSelectionModal}
        onClose={() => setShowWorkflowSelectionModal(false)}
        selectedWorkflows={createAgentFormData.workflows}
        onConfirm={(selectedWorkflowIds) => {
          setCreateAgentFormData({ workflows: selectedWorkflowIds });
        }}
      />

      {/* Device Selection Modal */}
      <DeviceSelectionModal
        isOpen={showDeviceSelectionModal}
        onClose={() => setShowDeviceSelectionModal(false)}
        selectedDeviceId={createAgentFormData.boundDeviceId || null}
        onConfirm={(deviceId) => {
          setCreateAgentFormData({ boundDeviceId: deviceId || '' });
        }}
      />

    </div>
  );
};

export default CreateAgentModal;
