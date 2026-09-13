/**
 * Workflow Editor Modal
 * Full-screen modal for editing workflows
 */

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import {
  X,
  Save,
  ChevronLeft,
  Settings,
  Loader2,
} from 'lucide-react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useToast } from '@/hooks/useToast';
import WorkflowEditor from '../WorkflowEditor';

interface WorkflowEditorModalProps {
  isOpen: boolean;
  workflowId?: string | null;
  onClose: () => void;
  onSave?: () => void;
}

const WorkflowEditorModal: React.FC<WorkflowEditorModalProps> = ({
  isOpen,
  workflowId,
  onClose,
  onSave,
}) => {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [showSettings, setShowSettings] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const {
    currentWorkflow,
    isLoadingCurrentWorkflow,
    isDirty,
    loadWorkflow,
    createWorkflow,
    saveWorkflow,
    updateWorkflowMeta,
    resetEditor,
  } = useWorkflowStore();

  // Load or create workflow when modal opens
  useEffect(() => {
    if (isOpen) {
      if (workflowId) {
        loadWorkflow(workflowId);
      } else {
        createWorkflow().catch(error => {
          console.error('Failed to create workflow:', error);
          showToast('error', i18n.t('workflow.messages.createFailed', '创建失败'), '');
        });
      }
    }
    return () => {
      if (!isOpen) {
        resetEditor();
      }
    };
  }, [isOpen, workflowId, loadWorkflow, createWorkflow, resetEditor, showToast]);

  const handleClose = () => {
    if (isDirty) {
      const confirmed = window.confirm(t('workflow.messages.unsavedChanges', '有未保存的更改，确定要关闭吗？'));
      if (!confirmed) return;
    }
    resetEditor();
    onClose();
  };

  const handleSave = async () => {
    if (!currentWorkflow || isSaving) return;

    setIsSaving(true);
    try {
      await saveWorkflow();
      showToast('success', t('workflow.messages.saveSuccess', '保存成功'), '');
      onSave?.();
    } catch {
      showToast('error', t('workflow.messages.saveFailed', '保存失败'), '');
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-gray-100 dark:bg-gray-900 flex flex-col">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-3 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={handleClose}
            className="flex items-center gap-1 text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
            <span className="text-sm">{t('common.back', '返回')}</span>
          </button>

          {isLoadingCurrentWorkflow ? (
            <div className="flex items-center gap-2 text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>{t('common.loading', '加载中...')}</span>
            </div>
          ) : currentWorkflow ? (
            <div className="flex items-center gap-3">
              <input
                type="text"
                value={currentWorkflow.name}
                onChange={(e) => updateWorkflowMeta({ name: e.target.value })}
                className="text-lg font-semibold text-gray-900 dark:text-gray-100 bg-transparent border-none focus:outline-none focus:ring-2 focus:ring-blue-500 rounded px-2 -mx-2"
                placeholder={t('workflow.namePlaceholder', '工作流名称')}
              />
              {isDirty && (
                <span className="text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/30 px-2 py-0.5 rounded">
                  {t('common.unsaved_changes', '未保存')}
                </span>
              )}
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          {/* Settings Toggle */}
          <button
            onClick={() => setShowSettings(!showSettings)}
            className={`p-2 rounded-lg transition-colors ${
              showSettings
                ? 'bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100'
                : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
            }`}
            title={t('workflow.settings', '设置')}
          >
            <Settings className="w-5 h-5" />
          </button>

          {/* Save Button */}
          <button
            onClick={handleSave}
            disabled={!isDirty || isSaving || !currentWorkflow}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 dark:bg-blue-700 text-white rounded-lg hover:bg-blue-700 dark:hover:bg-blue-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSaving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>{t('common.save', '保存')}</span>
          </button>

          {/* Close Button */}
          <button
            onClick={handleClose}
            className="p-2 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Editor */}
        <div className="flex-1 relative">
          {isLoadingCurrentWorkflow ? (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-50 dark:bg-gray-900">
              <div className="text-center">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500 mx-auto mb-3" />
                <p className="text-gray-600 dark:text-gray-400">
                  {t('workflow.loading', '加载工作流...')}
                </p>
              </div>
            </div>
          ) : (
            <WorkflowEditor />
          )}
        </div>

        {/* Settings Panel */}
        {showSettings && currentWorkflow && (
          <div className="w-80 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 flex flex-col">
            <div className="p-4 border-b border-gray-200 dark:border-gray-700">
              <h3 className="font-semibold text-gray-900 dark:text-gray-100">
                {t('workflow.settings', '工作流设置')}
              </h3>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('workflow.fields.name', '名称')}
                </label>
                <input
                  type="text"
                  value={currentWorkflow.name}
                  onChange={(e) => updateWorkflowMeta({ name: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-100"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('workflow.fields.description', '描述')}
                </label>
                <textarea
                  value={currentWorkflow.description || ''}
                  onChange={(e) => updateWorkflowMeta({ description: e.target.value })}
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-100 resize-none"
                />
              </div>

              {/* Status */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('workflow.fields.status', '状态')}
                </label>
                <select
                  value={currentWorkflow.status}
                  onChange={(e) => updateWorkflowMeta({ status: e.target.value as any })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-100"
                >
                  <option value="draft">{t('workflow.status.draft', '草稿')}</option>
                  <option value="active">{t('workflow.status.active', '已启用')}</option>
                  <option value="archived">{t('workflow.status.archived', '已归档')}</option>
                </select>
              </div>

              {/* Tags */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('workflow.fields.tags', '标签')}
                </label>
                <input
                  type="text"
                  value={currentWorkflow.tags.join(', ')}
                  onChange={(e) => updateWorkflowMeta({ 
                    tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean)
                  })}
                  placeholder={t('workflow.tagsPlaceholder', '用逗号分隔')}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-100"
                />
              </div>

              {/* Info */}
              <div className="pt-4 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400 space-y-1">
                <p>{t('workflow.fields.version', '版本')}: {currentWorkflow.version}</p>
                <p>{t('workflow.fields.total_nodes', '节点数')}: {currentWorkflow.definition.nodes.length}</p>
                <p>{t('common.tableCreated', '创建时间')}: {new Date(currentWorkflow.created_at).toLocaleString()}</p>
                <p>{t('common.lastUpdated', '更新时间')}: {new Date(currentWorkflow.updated_at).toLocaleString()}</p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default WorkflowEditorModal;

