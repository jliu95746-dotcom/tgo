/**
 * Workflow Toolbar Component
 * Contains simplified workflow actions for the top bar
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Save,
  Undo2,
  Redo2,
  CheckCircle,
  Loader2,
  Bug,
} from 'lucide-react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useToast } from '@/hooks/useToast';

/**
 * Workflow Toolbar Component
 */
const WorkflowToolbar: React.FC = () => {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [isSaving, setIsSaving] = useState(false);
  const [isValidating, setIsValidating] = useState(false);

  const {
    currentWorkflow,
    isDirty,
    history,
    historyIndex,
    saveWorkflow,
    undo,
    redo,
    validate,
    isDebugPanelOpen,
    setDebugPanelOpen,
    isExecuting,
  } = useWorkflowStore();

  const canUndo = historyIndex > 0;
  const canRedo = historyIndex < history.length - 1;

  const handleSave = async () => {
    if (!currentWorkflow || isSaving) return;

    setIsSaving(true);
    try {
      await saveWorkflow();
      showToast('success', t('workflow.messages.saveSuccess', '保存成功'), t('workflow.messages.saveSuccessDesc', '工作流已保存'));
    } catch {
      showToast('error', t('workflow.messages.saveFailed', '保存失败'), t('workflow.messages.saveFailedDesc', '保存工作流时发生错误'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleValidate = async () => {
    if (!currentWorkflow || isValidating) return;

    setIsValidating(true);
    try {
      const isValid = await validate();
      if (isValid) {
        showToast('success', t('workflow.messages.validateSuccess', '验证通过'), t('workflow.messages.validateSuccessDesc', '工作流配置正确'));
      } else {
        showToast('warning', t('workflow.messages.validateFailed', '验证失败'), t('workflow.messages.validateFailedDesc', '请检查工作流配置'));
      }
    } finally {
      setIsValidating(false);
    }
  };

  if (!currentWorkflow) return null;

  return (
    <div className="flex items-center gap-4">
      {/* Undo/Redo Group */}
      <div className="flex items-center bg-gray-100/50 dark:bg-gray-800/50 rounded-2xl p-1 border border-gray-200/50 dark:border-gray-700/50 shadow-sm transition-all hover:bg-gray-100/80 dark:hover:bg-gray-800/80">
        <button
          onClick={undo}
          disabled={!canUndo}
          className="p-2 text-gray-500 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-white dark:hover:bg-gray-700 rounded-xl disabled:opacity-30 disabled:cursor-not-allowed transition-all active:scale-90"
          title={t('workflow.actions.undo', '撤销')}
        >
          <Undo2 className="w-4.5 h-4.5" />
        </button>
        <div className="w-px h-5 bg-gray-200/80 dark:bg-gray-700/80 mx-1" />
        <button
          onClick={redo}
          disabled={!canRedo}
          className="p-2 text-gray-500 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-white dark:hover:bg-gray-700 rounded-xl disabled:opacity-30 disabled:cursor-not-allowed transition-all active:scale-90"
          title={t('workflow.actions.redo', '重做')}
        >
          <Redo2 className="w-4.5 h-4.5" />
        </button>
      </div>

      <div className="h-8 w-px bg-gray-200/50 dark:bg-gray-800/50 mx-1" />

      {/* Action Buttons Group */}
      <div className="flex items-center gap-3">
        {/* Debug */}
        <button
          onClick={() => setDebugPanelOpen(!isDebugPanelOpen)}
          className={`flex items-center gap-2 px-5 h-10 text-[11px] font-bold uppercase tracking-widest rounded-2xl transition-all shadow-sm border active:scale-95 ${
            isDebugPanelOpen 
              ? 'bg-orange-500 text-white border-orange-500 shadow-lg shadow-orange-200 dark:shadow-none' 
              : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-orange-500 hover:text-orange-600 dark:hover:text-orange-400'
          }`}
        >
          {isExecuting ? (
            <Loader2 className={`w-4 h-4 animate-spin ${isDebugPanelOpen ? 'text-white' : 'text-orange-500'}`} />
          ) : (
            <Bug className="w-4 h-4" />
          )}
          <span className="hidden lg:inline">{t('workflow.actions.debug', '调试')}</span>
        </button>

        {/* Validate */}
        <button
          onClick={handleValidate}
          disabled={isValidating}
          className="flex items-center gap-2 px-5 h-10 text-[11px] font-bold uppercase tracking-widest text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:border-blue-500 hover:text-blue-600 dark:hover:text-blue-400 rounded-2xl transition-all disabled:opacity-50 shadow-sm active:scale-95"
        >
          {isValidating ? (
            <Loader2 className="w-4 h-4 animate-spin text-blue-500" />
          ) : (
            <CheckCircle className="w-4 h-4" />
          )}
          <span className="hidden lg:inline">{t('workflow.actions.validate', '验证')}</span>
        </button>

        {/* Save */}
        <button
          onClick={handleSave}
          disabled={!isDirty || isSaving}
          className={`flex items-center gap-2 px-6 h-10 text-[11px] font-bold uppercase tracking-widest text-white transition-all rounded-2xl shadow-lg active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100 ${
            isDirty 
              ? 'bg-blue-600 hover:bg-blue-700 shadow-blue-500/30 dark:shadow-none' 
              : 'bg-gray-400 dark:bg-gray-700 shadow-none'
          }`}
        >
          {isSaving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          <span>{t('workflow.actions.save', '保存')}</span>
          {isDirty && (
            <span className="flex h-2 w-2 relative ml-1">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-white"></span>
            </span>
          )}
        </button>
      </div>
    </div>
  );
};

export default WorkflowToolbar;
