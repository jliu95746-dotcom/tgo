/**
 * Debug Panel Component
 * Right-side panel for workflow online running and debugging
 */

import React, { useState, useMemo, useId, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { useTranslation } from 'react-i18next';
import {
  X,
  Play,
  StopCircle,
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  ChevronRight,
  ChevronDown,
  Info,
  Terminal,
  History,
  Trash2,
  RefreshCw,
} from 'lucide-react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useAuthStore } from '@/stores/authStore';
import type { ApprovalNodeData, InputNodeData, WorkflowInputVariable } from '@/types/workflow';
import { parseWorkflowInput, workflowInputIssues } from '@/utils/workflowInputs';
import EventTestPanel from './EventTestPanel';

const DebugPanel: React.FC = () => {
  const { t } = useTranslation();
  const {
    currentWorkflow,
    isExecuting,
    executionError,
    currentExecution,
    executionPanelMode,
    nodeExecutionMap,
    setDebugPanelOpen,
    startExecution,
    refreshExecution,
    cancelExecution,
    clearExecution,
    debugInput,
    setDebugInput,
  } = useWorkflowStore();

  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});
  const [preparingRun, setPreparingRun] = useState(false);
  const [runSaveError, setRunSaveError] = useState('');
  const preparingRef = useRef(false);
  const busy = isExecuting || preparingRun;
  const isHistoryView = executionPanelMode === 'history';
  const canCancelCurrent = currentExecution && !['completed', 'failed', 'rejected', 'expired', 'cancelled'].includes(currentExecution.status);
  const inputId = useId();

  const nodes = useMemo(() => currentWorkflow?.definition?.nodes || [], [currentWorkflow?.definition?.nodes]);
  const requiresCustomerSession = nodes.some(node => node.type === 'approval'
    && (node.data as ApprovalNodeData).after_approval_action === 'create_aftersales_review');
  
  // Find all input nodes and their variables
  const inputNodes = useMemo(() => {
    return nodes.filter(n => n.type === 'input');
  }, [nodes]);

  const inputVariables = useMemo(() => {
    const vars: (WorkflowInputVariable & { nodeId: string })[] = [];
    inputNodes.forEach(node => {
      const data = node.data as InputNodeData;
      if (data.input_variables && Array.isArray(data.input_variables)) {
        data.input_variables.forEach(v => {
          vars.push({ ...v, nodeId: node.id });
        });
      }
    });
    return vars;
  }, [inputNodes]);

  // Handle input changes
  const handleInputChange = (name: string, value: string, type: WorkflowInputVariable['type']) => {
    const typedValue = parseWorkflowInput(value, type);
    const nextInput = { ...debugInput };
    if (typedValue === undefined) delete nextInput[name];
    else nextInput[name] = typedValue;
    setDebugInput(nextInput);
  };

  const inputIssues = workflowInputIssues(inputVariables, debugInput);
  const scenario = currentWorkflow?.tags?.find(tag => tag === 'scenario:knowledge' || tag === 'scenario:aftersales')?.slice('scenario:'.length);
  const canUseExample = Boolean(scenario && inputVariables.some(variable => variable.name === 'question' && variable.type === 'string'));
  const handleRun = async () => {
    if (useWorkflowStore.getState().executionPanelMode === 'history'
      || requiresCustomerSession || inputIssues.length || preparingRef.current || isExecuting || !currentWorkflow) return;
    const owner = useAuthStore.getState().user;
    const stillCurrent = () => {
      const user = useAuthStore.getState().user;
      return owner && user?.id === owner.id && user?.project_id === owner.project_id && user?.role === owner.role
        && useWorkflowStore.getState().currentWorkflow?.id === currentWorkflow.id
        && useWorkflowStore.getState().executionPanelMode !== 'history';
    };
    preparingRef.current = true; setPreparingRun(true); setRunSaveError('');
    try {
      if (useWorkflowStore.getState().isDirty) await useWorkflowStore.getState().saveWorkflow();
      if (!stillCurrent()) return;
      if (useWorkflowStore.getState().isDirty) throw new Error(t('businessScenarios.workflow.changedDuringSave'));
      await startExecution(debugInput);
    } catch (error) {
      if (stillCurrent()) setRunSaveError(error instanceof Error ? error.message : t('businessScenarios.workflow.testSaveFailed'));
    } finally { preparingRef.current = false; setPreparingRun(false); }
  };

  const handleCancel = () => {
    cancelExecution();
  };

  const handleClear = () => {
    clearExecution();
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running':
        return <Loader2 className="w-4 h-4 animate-spin text-blue-500" />;
      case 'completed':
        return <CheckCircle2 className="w-4 h-4 text-green-500" />;
      case 'failed':
        return <XCircle className="w-4 h-4 text-red-500" />;
      case 'cancelled':
        return <StopCircle className="w-4 h-4 text-gray-400" />;
      case 'rejected':
      case 'expired':
        return <XCircle className="w-4 h-4 text-amber-500" />;
      default:
        return <Clock className="w-4 h-4 text-gray-300" />;
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'running': return t('workflow.debug.running', '正在运行...');
      case 'completed': return t('workflow.debug.success', '执行成功');
      case 'failed': return t('workflow.debug.failed', '执行失败');
      case 'cancelled': return t('workflow.debug.cancelled', '已取消');
      case 'pending': return t('workflow.debug.pending', '等待中');
      case 'waiting_approval': return t('workflow.debug.waitingApproval', '等待审批人确认');
      case 'pending_resume': return t('workflow.debug.pendingResume', '已批准，等待恢复执行');
      case 'rejected': return t('workflow.debug.rejected', '审批已拒绝');
      case 'expired': return t('workflow.debug.expired', '审批已超时');
      default: return status;
    }
  };

  // Sort nodes by execution time if available, or maintain a logical order
  const executedNodes = useMemo(() => {
    // If we have a map of executions (live updates), use it
    if (Object.keys(nodeExecutionMap).length > 0) {
      return Object.values(nodeExecutionMap).sort((a, b) => 
        new Date(a.started_at).getTime() - new Date(b.started_at).getTime()
      );
    }
    // Fallback to static node_executions if available (historical or final state)
    if (!currentExecution?.node_executions) return [];
    return [...currentExecution.node_executions].sort((a, b) => 
      new Date(a.started_at).getTime() - new Date(b.started_at).getTime()
    );
  }, [currentExecution, nodeExecutionMap]);
  const visibleExecutionError = executionError ||
    (currentExecution?.status === 'failed' ? currentExecution.error : null);

  const toggleNodeExpand = (nodeId: string) => {
    setExpandedNodes(prev => ({ ...prev, [nodeId]: !prev[nodeId] }));
  };

  if (!isHistoryView && currentWorkflow && nodes.some(node => node.type === 'event')) {
    return <EventTestPanel workflowId={currentWorkflow.id} onClose={() => setDebugPanelOpen(false)} />;
  }

  return (
    <div role="region" aria-label={t(isHistoryView ? 'workflow.debug.historyTitle' : 'businessScenarios.workflow.testTitle')} className="absolute inset-0 w-full sm:relative sm:inset-auto sm:w-[22rem] sm:shrink-0 bg-white dark:bg-gray-900 border-l border-gray-100 dark:border-gray-800 flex flex-col z-40 shadow-2xl transition-all animate-in slide-in-from-right duration-300">
      {/* Header */}
      <div className="px-8 py-6 border-b border-gray-50 dark:border-gray-800 flex items-center justify-between bg-white/80 dark:bg-gray-900/80 backdrop-blur-md sticky top-0 z-10 shadow-sm">
        <div className="flex items-center gap-4">
          <div className="p-2.5 rounded-2xl bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400 border border-orange-100 dark:border-orange-800/50 shadow-sm">
            {isHistoryView ? <History className="w-6 h-6" /> : <Terminal className="w-6 h-6" />}
          </div>
          <div>
            <h3 className="font-bold text-gray-900 dark:text-gray-100 text-sm tracking-tight">{t(isHistoryView ? 'workflow.debug.historyTitle' : 'businessScenarios.workflow.testTitle')}</h3>
            <p className="text-[10px] text-gray-400 font-bold">{t(isHistoryView ? 'workflow.debug.historySubtitle' : 'businessScenarios.workflow.testSubtitle')}</p>
          </div>
        </div>
        <button
          onClick={() => setDebugPanelOpen(false)}
          aria-label={t(isHistoryView ? 'workflow.debug.historyClose' : 'businessScenarios.workflow.closeTest')}
          className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-all active:scale-90"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="p-8 space-y-10">
          {/* Input Variables Section */}
          {!isHistoryView && <section className="space-y-6">
            <p className="text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.realTest')}</p>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-gray-400" />
                <h4 className="text-[11px] uppercase font-bold text-gray-400 tracking-[0.15em]">{t('workflow.debug.input_variables', '输入参数')}</h4>
              </div>
              {inputVariables.length > 0 && (
                <button 
                  onClick={() => setDebugInput({})}
                  disabled={busy}
                  className="text-[10px] font-bold text-gray-400 hover:text-blue-500 uppercase tracking-widest transition-colors"
                >
                  {t('workflow.debug.reset', '重置')}
                </button>
              )}
            </div>

            {canUseExample && <button type="button" disabled={busy} onClick={() => setDebugInput({ ...debugInput, question: t(`businessScenarios.workflow.exampleQuestions.${scenario}`) })}
              className="rounded-xl border border-purple-200 bg-purple-50 px-3 py-2 text-xs font-medium text-purple-600 disabled:opacity-50 dark:border-purple-800 dark:bg-purple-900/20 dark:text-purple-300">
              {t('businessScenarios.workflow.exampleQuestion')}
            </button>}

            {inputVariables.length > 0 ? (
              <div className="space-y-5">
                {inputVariables.map((v) => (
                  <div key={`${v.nodeId}-${v.name}`} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label htmlFor={`${inputId}-${v.nodeId}-${v.name}`} className="text-xs font-bold text-gray-700 dark:text-gray-300">
                        {v.description || v.name}
                        {v.required && <span className="ml-2 text-red-500">{t('businessScenarios.workflow.inputs.requiredLabel')}</span>}
                      </label>
                      <span className="text-[10px] text-blue-500 font-bold bg-blue-50 dark:bg-blue-900/20 px-1.5 py-0.5 rounded-md border border-blue-100 dark:border-blue-800/50">{t(`businessScenarios.workflow.inputs.types.${v.type}`)}</span>
                    </div>
                    {v.type === 'string' && (v.min_length != null || v.max_length != null) && <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {t('businessScenarios.workflow.inputs.textLimit', { min: v.min_length ?? 0, max: v.max_length ?? t('businessScenarios.workflow.inputs.unlimited') })}
                    </p>}
                    {v.type === 'number' && (v.minimum != null || v.maximum != null) && <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {t('businessScenarios.workflow.inputs.numberLimit', { min: v.minimum ?? t('businessScenarios.workflow.inputs.unlimited'), max: v.maximum ?? t('businessScenarios.workflow.inputs.unlimited') })}
                    </p>}
                    {v.type === 'string' && v.allowed_values ? (
                      <select
                        id={`${inputId}-${v.nodeId}-${v.name}`}
                        aria-required={v.required === true}
                        value={String(debugInput[v.name] ?? '')}
                        onChange={event => {
                          if (!event.target.value) {
                            const nextInput = { ...debugInput };
                            delete nextInput[v.name];
                            setDebugInput(nextInput);
                          } else handleInputChange(v.name, event.target.value, v.type);
                        }}
                        className="w-full px-4 py-2.5 bg-gray-50/50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all text-sm font-bold dark:text-gray-100 cursor-pointer shadow-sm focus:bg-white dark:focus:bg-gray-800"
                        disabled={busy}
                      >
                        <option value="">{t('businessScenarios.workflow.inputs.choose')}</option>
                        {v.allowed_values.map(choice => <option key={choice} value={choice}>{choice}</option>)}
                      </select>
                    ) : v.type === 'boolean' ? (
                      <select
                        id={`${inputId}-${v.nodeId}-${v.name}`}
                        aria-required={v.required === true}
                        value={String(debugInput[v.name] ?? '')}
                        onChange={(e) => handleInputChange(v.name, e.target.value, v.type)}
                        className="w-full px-4 py-2.5 bg-gray-50/50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all text-sm font-bold dark:text-gray-100 cursor-pointer shadow-sm focus:bg-white dark:focus:bg-gray-800"
                        disabled={busy}
                      >
                        <option value="">{t('businessScenarios.workflow.inputs.choose')}</option>
                        <option value="true">{t('common.yes', '是')}</option>
                        <option value="false">{t('common.no', '否')}</option>
                      </select>
                    ) : (
                      <input
                        id={`${inputId}-${v.nodeId}-${v.name}`}
                        aria-required={v.required === true}
                        type={v.type === 'number' ? 'number' : 'text'}
                        value={debugInput[v.name] ?? ''}
                        onChange={(e) => handleInputChange(v.name, e.target.value, v.type)}
                        className="w-full px-4 py-2.5 bg-gray-50/50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all text-sm font-bold dark:text-gray-100 shadow-sm focus:bg-white dark:focus:bg-gray-800"
                        placeholder={`${t('common.please_input', '请输入')  } ${v.description || v.name}...`}
                        disabled={busy}
                      />
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-8 bg-gray-50/50 dark:bg-gray-800/50 rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 flex flex-col items-center justify-center gap-3">
                <div className="w-12 h-12 bg-gray-100 dark:bg-gray-800 rounded-2xl flex items-center justify-center text-gray-300 dark:text-gray-600">
                  <Terminal className="w-6 h-6" />
                </div>
                <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest">{t('workflow.debug.no_input_variables', '无输入参数')}</p>
              </div>
            )}

            {inputIssues.length > 0 && <div role="status" className="space-y-1 text-xs text-red-600 dark:text-red-400">
              {inputIssues.map(issue => <p key={issue.field}>{t(`businessScenarios.workflow.inputs.${issue.code}`, { field: inputVariables.find(variable => variable.name === issue.field)?.description || issue.field })}</p>)}
            </div>}
            {requiresCustomerSession && <p role="status" className="rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">{t('businessScenarios.workflow.reviewTestingLimit')}</p>}
            {runSaveError && <p role="alert" className="break-words rounded-xl bg-red-50 p-3 text-xs text-red-600 dark:bg-red-900/20">{runSaveError}</p>}
            <div className="pt-2 flex gap-3">
              {!isExecuting && !canCancelCurrent ? (
                <button
                  onClick={handleRun}
                  disabled={requiresCustomerSession || inputIssues.length > 0 || preparingRun}
                  className="flex-1 flex items-center justify-center gap-3 px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-2xl font-bold text-sm transition-all shadow-lg shadow-blue-500/30 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {preparingRun ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
                  {t('workflow.debug.run', '运行工作流')}
                </button>
              ) : (
                <button
                  onClick={handleCancel}
                  className="flex-1 flex items-center justify-center gap-3 px-6 py-3 bg-red-500 hover:bg-red-600 text-white rounded-2xl font-bold text-sm transition-all shadow-lg shadow-red-500/30 active:scale-95"
                >
                  <StopCircle className="w-4 h-4" />
                  {isExecuting ? t('workflow.debug.stop', '停止运行') : t('workflow.debug.cancelWaiting', '取消本次运行')}
                </button>
              )}
            </div>
          </section>}

          {/* Execution Status Section */}
          {(currentExecution || isExecuting || executionError) && (
            <section className="space-y-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <History className="w-4 h-4 text-gray-400" />
                  <h4 className="text-[11px] uppercase font-bold text-gray-400 tracking-[0.15em]">{t('workflow.debug.execution_status', '运行历史')}</h4>
                </div>
                {!isHistoryView && !isExecuting && !canCancelCurrent && (
                  <button 
                    onClick={handleClear}
                    className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-all text-gray-400 hover:text-red-500"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>

              {currentExecution?.status === 'waiting_approval' && (
                <p role="status" className="rounded-xl bg-purple-50 p-3 text-xs leading-5 text-purple-700 dark:bg-purple-950/30 dark:text-purple-300">
                  {t('workflow.debug.approvalHint', '流程已暂停。指定审批人可在工作流管理的“我的审批”处理；处理后刷新这里的运行状态。')}
                </p>
              )}
              {currentExecution && !isExecuting && (
                <button type="button" onClick={() => void refreshExecution()}
                  className="inline-flex items-center gap-2 rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800">
                  <RefreshCw className="h-3.5 w-3.5" />
                  {t('workflow.debug.refreshStatus', '刷新运行状态')}
                </button>
              )}

              <div className="bg-gray-50/50 dark:bg-gray-800/50 rounded-2xl border border-gray-100 dark:border-gray-700 overflow-hidden shadow-sm">
                <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-white/50 dark:bg-gray-900/50">
                  <div className="flex items-center gap-2.5">
                    {currentExecution ? getStatusIcon(currentExecution.status) : isExecuting
                      ? <Loader2 className="w-4 h-4 animate-spin text-blue-500" />
                      : <XCircle className="w-4 h-4 text-red-500" />}
                    <span className="text-[11px] font-bold text-gray-700 dark:text-gray-200 uppercase tracking-widest">
                      {currentExecution ? getStatusText(currentExecution.status) : isExecuting
                        ? t('workflow.debug.starting', '启动中...')
                        : t('workflow.debug.incomplete', '执行未完成')}
                    </span>
                  </div>
                  {currentExecution?.duration && (
                    <span className="text-[10px] text-gray-400 font-mono font-bold bg-white dark:bg-gray-800 px-1.5 py-0.5 rounded border border-gray-100 dark:border-gray-700">
                      {(currentExecution.duration / 1000).toFixed(2)}s
                    </span>
                  )}
                </div>

                {visibleExecutionError && (
                  <div className="p-4 bg-red-50 dark:bg-red-900/10 text-red-600 dark:text-red-400 text-[11px] leading-relaxed">
                    <p className="font-bold mb-1 flex items-center gap-1">
                      <XCircle className="w-3 h-3" />
                      {t('workflow.debug.error', '执行错误')}
                    </p>
                    {visibleExecutionError}
                  </div>
                )}

                {/* Timeline */}
                <div className="p-4 space-y-4">
                  {executedNodes.length > 0 ? (
                    <div className="space-y-3 relative before:absolute before:left-[7px] before:top-2 before:bottom-2 before:w-[1px] before:bg-gray-200 dark:before:bg-gray-700">
                      {executedNodes.map((ne) => (
                        <div key={ne.id} className="relative pl-6">
                          <div className="absolute left-0 top-1.5 w-3.5 h-3.5 rounded-full bg-white dark:bg-gray-900 flex items-center justify-center z-10">
                            {getStatusIcon(ne.status)}
                          </div>
                          <div className="group">
                            <div 
                              className="flex items-center justify-between cursor-pointer"
                              onClick={() => toggleNodeExpand(ne.node_id)}
                            >
                              <span className="text-xs font-medium text-gray-700 dark:text-gray-200 group-hover:text-blue-500 transition-colors">
                                {nodes.find(n => n.id === ne.node_id)?.data.label || ne.node_type}
                              </span>
                              <div className="flex items-center gap-2">
                                {ne.duration !== undefined && ne.duration !== null && (
                                  <span className="text-[9px] text-gray-400 font-mono">
                                    {ne.duration < 1000 ? `${ne.duration}ms` : `${(ne.duration / 1000).toFixed(1)}s`}
                                  </span>
                                )}
                                {expandedNodes[ne.node_id] ? <ChevronDown className="w-3 h-3 text-gray-400" /> : <ChevronRight className="w-3 h-3 text-gray-400" />}
                              </div>
                            </div>
                            
                            {expandedNodes[ne.node_id] && (
                              <div 
                                className="mt-2 space-y-2 animate-in fade-in slide-in-from-top-1 duration-200"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {ne.completed_at && (
                                  <div className="text-[10px] text-gray-500 dark:text-gray-400 font-mono space-y-0.5">
                                    <div>{t('workflow.debug.startedAt', '开始')}：{new Date(ne.started_at).toLocaleString('zh-CN', { hour12: false })}</div>
                                    <div>{t('workflow.debug.completedAt', '结束')}：{new Date(ne.completed_at).toLocaleString('zh-CN', { hour12: false })}</div>
                                  </div>
                                )}
                                {ne.input && (
                                  <div className="space-y-1">
                                    <span className="text-[9px] text-gray-400 font-bold uppercase tracking-wider">{t('workflow.debug.input', 'Input')}</span>
                                    <pre className="p-2 bg-gray-900 dark:bg-black rounded-lg text-[10px] text-blue-400 overflow-x-auto font-mono">
                                      {JSON.stringify(ne.input, null, 2)}
                                    </pre>
                                  </div>
                                )}
                                {ne.output && (
                                  <div className="space-y-1">
                                    <span className="text-[9px] text-gray-400 font-bold uppercase tracking-wider">{t('workflow.debug.output', 'Output')}</span>
                                    <pre className="p-2 bg-gray-900 dark:bg-black rounded-lg text-[10px] text-green-400 overflow-x-auto font-mono">
                                      {JSON.stringify(ne.output, null, 2)}
                                    </pre>
                                  </div>
                                )}
                                {ne.error && (
                                  <div className="space-y-1">
                                    <span className="text-[9px] text-red-400 font-bold uppercase tracking-wider">{t('workflow.debug.error', 'Error')}</span>
                                    <div className="p-2 bg-red-50 dark:bg-red-900/20 rounded-lg text-[10px] text-red-600 dark:text-red-400 font-mono">
                                      {ne.error}
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-8 opacity-50">
                      <Terminal className="w-8 h-8 text-gray-300 mb-2" />
                      <p className="text-[10px] text-gray-400 font-medium">{t('workflow.debug.waiting', '等待执行...')}</p>
                    </div>
                  )}
                </div>
              </div>
            </section>
          )}

          {/* Final Output Section */}
          {currentExecution?.status === 'completed' && currentExecution.output != null && (
            <section className="space-y-4">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-green-500" />
                <h4 className="text-[11px] uppercase font-bold text-gray-400 tracking-wider">{t('workflow.debug.final_output', '最终输出')}</h4>
              </div>
              <div className="p-4 bg-green-50/50 dark:bg-green-900/10 rounded-2xl border border-green-100 dark:border-green-900/30">
                {typeof currentExecution.output === 'string' ? (
                  <div className="break-words text-sm leading-6 text-green-700 dark:text-green-400 [&_p+p]:mt-3 [&_strong]:font-semibold">
                    <ReactMarkdown>{currentExecution.output}</ReactMarkdown>
                  </div>
                ) : (
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-green-700 dark:text-green-400">
                    {JSON.stringify(currentExecution.output, null, 2)}
                  </pre>
                )}
              </div>
            </section>
          )}
        </div>
      </div>

      {/* Persistence Note */}
      <div className="p-4 bg-blue-50/30 dark:bg-blue-900/5 border-t border-gray-50 dark:border-gray-800">
        <div className="flex items-start gap-2">
          <Info className="w-3 h-3 text-blue-500 mt-0.5" />
          <p className="text-[9px] text-gray-400 leading-relaxed">
            {isHistoryView ? t('workflow.debug.historyNote')
              : t('workflow.debug.persistence_note', '调试模式下的执行结果仅供预览，不会影响线上运行的工作流版本。')}
          </p>
        </div>
      </div>
    </div>
  );
};

export default DebugPanel;

