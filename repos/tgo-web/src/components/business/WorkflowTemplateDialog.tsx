import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Loader2 } from 'lucide-react';
import { useAIStore } from '@/stores/aiStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { WorkflowApiService } from '@/services/workflowApi';
import { buildScenarioWorkflow } from '@/utils/businessScenarios';
import type { BusinessScenario, ScenarioWorkflowOptions } from '@/types/businessScenarios';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { selectWorkflowAgent } from '@/utils/workflowSetup';
import WorkflowAgentSelection from './WorkflowAgentSelection';
import { useAgentBusinessConnectionStore } from '@/stores/agentBusinessConnectionStore';
import { useAuthStore } from '@/stores/authStore';
import { staffApi } from '@/services/staffApi';
import type { StaffResponse } from '@/services/api';

export default function WorkflowTemplateDialog({ scenario, onClose, onCreated }: {
  scenario: BusinessScenario; onClose: () => void; onCreated: (id: string) => void;
}) {
  const { t } = useTranslation();
  const { agents, refreshAgents, agentsError, isLoadingAgents } = useAIStore();
  const [name, setName] = useState(t(`businessScenarios.scenes.${scenario.id}.title`));
  const [agentId, setAgentId] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [reviewMode, setReviewMode] = useState<'policy' | 'internal_review'>('policy');
  const [approverStaffId, setApproverStaffId] = useState('');
  const [caseCategory, setCaseCategory] = useState<NonNullable<ScenarioWorkflowOptions['review']>['caseCategory']>('return_request');
  const [approvers, setApprovers] = useState<StaffResponse[]>([]);
  const [approversLoading, setApproversLoading] = useState(false);
  const [approversError, setApproversError] = useState(false);
  const projectId = useAuthStore(state => state.user?.project_id);
  const business = useAgentBusinessConnectionStore();
  const manuallySelected = useRef(false);
  const [prefilled, setPrefilled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialogRef = useDialogFocus<HTMLFormElement>(() => { if (!busy) onClose(); });
  useEffect(() => {
    if (scenario.id !== 'aftersales' || reviewMode !== 'internal_review') void refreshAgents();
  }, [refreshAgents, scenario.id, reviewMode]);
  useEffect(() => {
    if (scenario.id !== 'logistics' || !agentId) return;
    setConnectionId('');
    void useAgentBusinessConnectionStore.getState().load(agentId);
  }, [scenario.id, agentId]);
  useEffect(() => {
    if (isLoadingAgents || agentsError || useAIStore.getState().isLoadingAgents) return;
    const next = selectWorkflowAgent(agentId, manuallySelected.current, agents);
    if (next !== agentId) { setAgentId(next); setPrefilled(true); }
  }, [agents, isLoadingAgents, agentsError, agentId]);
  useEffect(() => {
    if (scenario.id !== 'aftersales' || reviewMode !== 'internal_review' || !projectId) return;
    let cancelled = false;
    setApproversLoading(true);
    setApproversError(false);
    const loadApprovers = async () => {
      try {
        const rows: StaffResponse[] = [];
        for (let offset = 0; offset < 1000; offset += 100) {
          const page = await staffApi.listStaff({ limit: 100, offset });
          rows.push(...page.data);
          if (!page.pagination.has_next) break;
        }
        if (!cancelled) setApprovers(rows.filter(person => person.project_id === projectId
          && person.role !== 'agent' && person.account_enabled !== false));
      } catch {
        if (!cancelled) setApproversError(true);
      } finally {
        if (!cancelled) setApproversLoading(false);
      }
    };
    void loadApprovers();
    return () => { cancelled = true; };
  }, [scenario.id, reviewMode, projectId]);
  const wantsReview = scenario.id === 'aftersales' && reviewMode === 'internal_review';
  const approverReady = !wantsReview || (!approversLoading && !approversError
    && approvers.some(person => person.id === approverStaffId && person.project_id === projectId));
  const readyConnections = scenario.id === 'logistics' && business.agentId === agentId
    ? business.connections.filter(connection => connection.enabled && connection.mode === 'http'
      && connection.test_status === 'passed'
      && connection.capabilities.includes('order_query') && connection.capabilities.includes('logistics_query')
      && business.bindings.some(binding => binding.enabled && binding.connection_id === connection.id
        && binding.operations.includes('order_query') && binding.operations.includes('logistics_query')))
    : [];
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError(''); setBusy(true);
    try {
      if (!wantsReview && (isLoadingAgents || agentsError || !agents.some(agent => agent.id === agentId))) throw new Error(t('businessScenarios.workflow.chooseAgent'));
      if (scenario.id === 'logistics' && !readyConnections.some(connection => connection.id === connectionId)) throw new Error(t('businessScenarios.workflow.chooseConnection'));
      if (!approverReady) throw new Error(t('businessScenarios.workflow.reviewAssigneeRequired'));
      const draft = await WorkflowApiService.createWorkflow(buildScenarioWorkflow(scenario.id, {
        name, agentId, connectionId,
        review: wantsReview ? { approverStaffId, caseCategory } : undefined,
      }));
      useWorkflowStore.getState().setCurrentWorkflow(draft);
      void useWorkflowStore.getState().loadWorkflows();
      onCreated(draft.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : t('businessScenarios.workflow.failed')); }
    finally { setBusy(false); }
  };
  const inputClass = 'mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <form ref={dialogRef} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="workflow-template-title" className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-3xl bg-white p-6 shadow-2xl dark:bg-gray-900">
      <div className="flex items-center justify-between"><h3 id="workflow-template-title" className="text-lg font-bold text-gray-900 dark:text-white">{t(`businessScenarios.scenes.${scenario.id}.title`)}</h3><button type="button" disabled={busy} onClick={onClose} aria-label={t('businessScenarios.close')} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100"><X size={20} /></button></div>
      <p className="my-4 rounded-xl bg-purple-50 p-3 text-sm text-purple-700 dark:bg-purple-900/20 dark:text-purple-300">{t(wantsReview ? 'businessScenarios.workflow.reviewSteps' : `businessScenarios.scenes.${scenario.id}.steps`)}</p>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">{t('businessScenarios.workflow.name')}<input disabled={busy} required maxLength={120} value={name} onChange={event => setName(event.target.value)} className={inputClass} /></label>
      {!wantsReview && <WorkflowAgentSelection value={agentId} disabled={busy} prefilled={prefilled} onChange={id => { manuallySelected.current = true; setPrefilled(false); setAgentId(id); }} />}
      {scenario.id === 'aftersales' && <div className="mt-4 space-y-3 text-sm text-gray-700 dark:text-gray-200">
        <label htmlFor="aftersales-template-mode" className="font-medium">{t('businessScenarios.workflow.aftersalesMode')}</label>
        <select id="aftersales-template-mode" value={reviewMode} disabled={busy} onChange={event => {
          const nextMode = event.target.value as typeof reviewMode;
          if (name === t('businessScenarios.scenes.aftersales.title') || name === t('businessScenarios.workflow.reviewName')) {
            setName(t(nextMode === 'internal_review' ? 'businessScenarios.workflow.reviewName' : 'businessScenarios.scenes.aftersales.title'));
          }
          setReviewMode(nextMode);
        }} className={inputClass}>
          <option value="policy">{t('businessScenarios.workflow.aftersalesPolicyOnly')}</option>
          <option value="internal_review">{t('businessScenarios.workflow.aftersalesInternalReview')}</option>
        </select>
        {wantsReview && <div className="space-y-3 rounded-xl border border-purple-200 bg-purple-50/60 p-4 dark:border-purple-900 dark:bg-purple-900/10">
          <p className="text-xs leading-5 text-purple-800 dark:text-purple-300">{t('businessScenarios.workflow.reviewScope')}</p>
          <label htmlFor="aftersales-template-approver" className="block font-medium">{t('businessScenarios.workflow.reviewAssignee')}</label>
          <select id="aftersales-template-approver" required value={approverStaffId} disabled={busy || approversLoading || approversError} onChange={event => setApproverStaffId(event.target.value)} className={inputClass}>
            <option value="">{t('businessScenarios.workflow.reviewChooseAssignee')}</option>
            {approvers.map(person => <option key={person.id} value={person.id}>{person.nickname || person.username}</option>)}
          </select>
          {approversLoading && <p role="status" className="text-xs text-gray-500">{t('businessScenarios.workflow.reviewStaffLoading')}</p>}
          {approversError && <p role="alert" className="text-xs text-red-600">{t('businessScenarios.workflow.reviewStaffLoadFailed')}</p>}
          {!approversLoading && !approversError && approvers.length === 0 && <p className="text-xs text-amber-700">{t('businessScenarios.workflow.reviewStaffEmpty')}</p>}
          <label htmlFor="aftersales-template-category" className="block font-medium">{t('businessScenarios.workflow.reviewCategory')}</label>
          <select id="aftersales-template-category" value={caseCategory} disabled={busy} onChange={event => setCaseCategory(event.target.value as typeof caseCategory)} className={inputClass}>
            <option value="return_request">{t('businessScenarios.workflow.approvalConfig.categories.return_request')}</option>
            <option value="exchange_request">{t('businessScenarios.workflow.approvalConfig.categories.exchange_request')}</option>
            <option value="quality_issue">{t('businessScenarios.workflow.approvalConfig.categories.quality_issue')}</option>
          </select>
          <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t('businessScenarios.workflow.reviewTestingLimit')}</p>
        </div>}
      </div>}
      {scenario.id === 'logistics' && <div className="mt-4 space-y-2 text-sm text-gray-700 dark:text-gray-200">
        <label htmlFor="workflow-business-connection" className="font-medium">{t('businessScenarios.workflow.queryConnection')}</label>
        <select id="workflow-business-connection" value={connectionId} disabled={busy || business.loading || business.agentId !== agentId} onChange={event => setConnectionId(event.target.value)} className={inputClass}>
          <option value="">{t('businessScenarios.workflow.chooseConnection')}</option>
          {readyConnections.map(connection => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
        </select>
        {business.loading && <p className="text-xs text-gray-500">{t('businessScenarios.workflow.loadingConnections')}</p>}
        {business.error && <p role="alert" className="text-xs text-red-600">{t('businessScenarios.workflow.connectionLoadFailed')}</p>}
        {business.hasMore && <button type="button" disabled={business.loading || business.loadingMore} onClick={() => void business.loadMore()} className="text-xs font-medium text-blue-600 disabled:opacity-50">{t('businessConnections.more')}</button>}
        {!business.loading && !business.error && !business.hasMore && readyConnections.length === 0 && <p className="text-xs text-amber-700 dark:text-amber-300">{t('businessScenarios.workflow.noReadyConnection')}</p>}
        <p className="text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.queryPrivateHint')}</p>
      </div>}
      {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{error}</p>}
      <p className="mt-5 text-xs text-gray-500">{t('businessScenarios.workflow.draft')}</p>
      <button disabled={busy || (!wantsReview && (isLoadingAgents || Boolean(agentsError) || !agents.some(agent => agent.id === agentId))) || !approverReady || (scenario.id === 'logistics' && !readyConnections.some(connection => connection.id === connectionId))} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-purple-600 py-3 font-medium text-white hover:bg-purple-700 disabled:opacity-50">{busy && <Loader2 size={18} className="animate-spin" />}{t('businessScenarios.workflow.create')}</button>
    </form>
  </div>;
}
