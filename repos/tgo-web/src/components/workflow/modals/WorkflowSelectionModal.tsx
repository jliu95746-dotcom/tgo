/**
 * Workflow Selection Modal
 * Modal for selecting workflows to associate with an agent
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import {
  X,
  Search,
  ExternalLink,
  GitBranch,
  RefreshCw,
  Plus,
} from 'lucide-react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useToast } from '@/hooks/useToast';
import type { WorkflowSummary } from '@/types/workflow';

interface WorkflowSelectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedWorkflows: string[];
  onConfirm: (selectedWorkflowIds: string[]) => void;
}

const WorkflowSelectionModal: React.FC<WorkflowSelectionModalProps> = ({
  isOpen,
  onClose,
  selectedWorkflows,
  onConfirm,
}) => {
  const { t } = useTranslation();
  const dialogRef = useDialogFocus<HTMLDivElement>(onClose, isOpen);
  const { showToast } = useToast();
  
  const {
    workflows,
    isLoadingWorkflows,
    workflowsError,
    loadWorkflows,
  } = useWorkflowStore();

  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebouncedValue(searchQuery, 300);
  const [tempSelectedWorkflows, setTempSelectedWorkflows] = useState<string[]>([]);

  // Load workflows when modal opens
  useEffect(() => {
    if (isOpen && workflows.length === 0) {
      loadWorkflows().catch(error => {
        console.error('Failed to load workflows:', error);
        showToast('error', t('common.loadFailed', '加载失败'), t('workflow.selectModal.loadFailedDesc', '无法加载工作流列表'));
      });
    }
  }, [isOpen, workflows.length, loadWorkflows, showToast, t]);

  // Initialize temp selection when modal opens
  useEffect(() => {
    if (isOpen) {
      setTempSelectedWorkflows([...selectedWorkflows]);
    }
  }, [isOpen, selectedWorkflows]);

  // Filter workflows
  const filteredWorkflows = useMemo(() => {
    let filtered = workflows;
    
    if (debouncedSearch.trim()) {
      const query = debouncedSearch.toLowerCase();
      filtered = filtered.filter(wf =>
        wf.name.toLowerCase().includes(query) ||
        (wf.description && wf.description.toLowerCase().includes(query))
      );
    }
    
    return filtered;
  }, [workflows, debouncedSearch]);

  const handleWorkflowClick = (workflow: WorkflowSummary) => {
    setTempSelectedWorkflows(prev => {
      if (prev.includes(workflow.id)) {
        return prev.filter(id => id !== workflow.id);
      }
      return [...prev, workflow.id];
    });
  };

  const handleConfirm = () => {
    onConfirm(tempSelectedWorkflows);
    onClose();
  };

  const handleCancel = () => {
    setTempSelectedWorkflows([...selectedWorkflows]);
    onClose();
  };


  const handleRetry = () => {
    loadWorkflows().catch(error => {
      console.error('Failed to retry loading workflows:', error);
      showToast('error', t('common.retryFailed', '重试失败'), t('workflow.selectModal.retryFailedDesc', '无法加载工作流列表'));
    });
  };

  const getStatusBadge = (status: string) => {
    const styles: Record<string, string> = {
      active: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
      draft: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300',
      archived: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
    };
    const labels: Record<string, string> = {
      active: t('workflow.status.active', '已启用'),
      draft: t('workflow.status.draft', '草稿'),
      archived: t('workflow.status.archived', '已归档'),
    };
    return (
      <span className={`px-2 py-0.5 text-xs rounded-full ${styles[status] || styles.draft}`}>
        {labels[status] || status}
      </span>
    );
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="workflow-selection-title" className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <h3 id="workflow-selection-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {t('workflow.selectModal.title', '选择工作流')}
          </h3>
          <button
            onClick={onClose}
            aria-label={t('common.close', '关闭')}
            className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search Bar */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 dark:text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('workflow.selectModal.searchPlaceholder', '搜索工作流...')}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-100"
            />
          </div>
        </div>

        {/* Workflow List */}
        <div className="flex-1 overflow-y-auto min-h-0 dark:bg-gray-900">
          <div className="p-4">
            {/* Loading State */}
            {isLoadingWorkflows && (
              <div className="flex items-center justify-center py-8">
                <div className="text-center">
                  <RefreshCw className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500 mx-auto mb-2" />
                  <p className="text-gray-500 dark:text-gray-400 text-sm">
                    {t('workflow.selectModal.loading', '正在加载工作流...')}
                  </p>
                </div>
              </div>
            )}

            {/* Error State */}
            {workflowsError && !isLoadingWorkflows && (
              <div className="flex items-center justify-center py-8">
                <div className="text-center">
                  <div className="w-12 h-12 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center mx-auto mb-3">
                    <X className="w-6 h-6 text-red-600 dark:text-red-400" />
                  </div>
                  <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1">
                    {t('common.loadFailed', '加载失败')}
                  </h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{workflowsError}</p>
                  <button
                    onClick={handleRetry}
                    className="px-3 py-1.5 bg-blue-500 dark:bg-blue-600 text-white text-xs rounded hover:bg-blue-600 dark:hover:bg-blue-700 transition-colors"
                  >
                    {t('common.retry', '重试')}
                  </button>
                </div>
              </div>
            )}

            {/* Workflow List */}
            {!isLoadingWorkflows && !workflowsError && (
              <div className="space-y-2">
                {filteredWorkflows.length === 0 ? (
                  <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                    <GitBranch className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    <p>{t('workflow.selectModal.noMatch', '未找到匹配的工作流')}</p>
                    <Link to="/ai/workflows" target="_blank" rel="noopener noreferrer"
                      className="mt-3 text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 text-sm flex items-center gap-1 mx-auto"
                    >
                      <Plus className="w-4 h-4" />
                      <span>{t('workflow.selectModal.createNew', '创建新工作流')}</span>
                    </Link>
                  </div>
                ) : (
                  filteredWorkflows.map(workflow => {
                    const isSelected = tempSelectedWorkflows.includes(workflow.id);
                    return (
                      <div
                        key={workflow.id}
                        onClick={() => handleWorkflowClick(workflow)}
                        className={`
                          flex items-center justify-between p-3 rounded-md border cursor-pointer transition-colors
                          ${isSelected
                            ? 'bg-blue-50 border-blue-200 hover:bg-blue-100 dark:bg-blue-900/30 dark:border-blue-700 dark:hover:bg-blue-900/50'
                            : 'bg-white border-gray-200 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-700 dark:hover:bg-gray-700'
                          }
                        `}
                      >
                        <div className="flex items-start gap-3 flex-1 min-w-0">
                          <div className={`
                            w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0
                            ${isSelected
                              ? 'bg-blue-500 text-white'
                              : 'bg-purple-100 text-purple-600 dark:bg-purple-900/50 dark:text-purple-400'
                            }
                          `}>
                            <GitBranch className="w-5 h-5" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className={`text-sm font-semibold ${
                                isSelected ? 'text-blue-900 dark:text-blue-200' : 'text-gray-800 dark:text-gray-100'
                              }`}>
                                {workflow.name}
                              </span>
                              {getStatusBadge(workflow.status)}
                            </div>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
                              {workflow.description || t('workflow.noDescription', '暂无描述')}
                            </p>
                            <div className="flex items-center gap-3 mt-2 text-xs text-gray-400 dark:text-gray-500">
                              <span>v{workflow.version}</span>
                              {workflow.tags.length > 0 && (
                                <span>{workflow.tags.slice(0, 2).join(', ')}</span>
                              )}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 ml-2">
                          <input
                            type="checkbox"
                            aria-label={workflow.name}
                            onClick={event => event.stopPropagation()}
                            checked={isSelected}
                            onChange={() => handleWorkflowClick(workflow)}
                            className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500 dark:bg-gray-700"
                          />
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        </div>

        {/* Manage Link */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
          <Link to="/ai/workflows" target="_blank" rel="noopener noreferrer"
            className="flex items-center space-x-2 text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 text-sm transition-colors"
          >
            <span>{t('workflow.selectModal.manageWorkflows', '管理工作流')}</span>
            <ExternalLink className="w-4 h-4" />
          </Link>
        </div>

        {/* Footer */}
        <div className="flex justify-end space-x-3 p-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 flex-shrink-0">
          <button
            onClick={handleCancel}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-600"
          >
            {t('common.cancel', '取消')}
          </button>
          <button
            onClick={handleConfirm}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 dark:bg-blue-700 border border-transparent rounded-md hover:bg-blue-700 dark:hover:bg-blue-800"
          >
            {t('workflow.selectModal.confirmSelection', '确认选择 ({{count}})', { count: tempSelectedWorkflows.length })}
          </button>
        </div>
      </div>
    </div>
  );
};

export default WorkflowSelectionModal;

