/**
 * Workflow Editor Page
 * Standalone page for editing workflows via route - Minimal Clean Redesign
 */

import React, { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ChevronLeft,
  Settings,
  Loader2,
  ChevronRight,
  Info,
  GitBranch,
} from 'lucide-react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useToast } from '@/hooks/useToast';
import { WorkflowEditor, WorkflowToolbar } from '@/components/workflow';
import NodePalette from '@/components/workflow/NodePalette';
import NodeConfigPanel from '@/components/workflow/panels/NodeConfigPanel';
import DebugPanel from '@/components/workflow/panels/DebugPanel';
import WorkflowBasicConfiguration from '@/components/business/WorkflowBasicConfiguration';

const WorkflowEditorPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [showSettings, setShowSettings] = useState(false);
  const [editorMode, setEditorMode] = useState<'basic' | 'advanced'>('advanced');
  const [paletteCollapsed, setPaletteCollapsed] = useState(() => window.matchMedia('(max-width: 899px)').matches);

  const {
    currentWorkflow,
    isLoadingCurrentWorkflow,
    currentWorkflowError,
    isDirty,
    loadWorkflow,
    createWorkflow,
    updateWorkflowMeta,
    resetEditor,
    selectedNodeId,
    setSelectedNode,
    isDebugPanelOpen,
    setDebugPanelOpen,
  } = useWorkflowStore();

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null;
    return currentWorkflow?.definition.nodes.find(n => n.id === selectedNodeId) || null;
  }, [currentWorkflow, selectedNodeId]);

  const creatingRef = React.useRef(false);
  const initializedMode = React.useRef<string | null>(null);
  useEffect(() => {
    if (currentWorkflow && initializedMode.current !== currentWorkflow.id) {
      initializedMode.current = currentWorkflow.id;
      setEditorMode(currentWorkflow.tags?.some(tag => tag.startsWith('scenario:')) ? 'basic' : 'advanced');
    }
  }, [currentWorkflow]);

  // Load or create workflow based on ID
  useEffect(() => {
    if (id && id !== 'new') {
      loadWorkflow(id);
    } else if (id === 'new' && !creatingRef.current) {
      creatingRef.current = true;
      createWorkflow().then(newWorkflow => {
        // Once created, navigate to the edit route with the real ID
        // to prevent re-creation on refresh/remount
        navigate(`/ai/workflows/${newWorkflow.id}/edit`, { replace: true });
      }).catch(error => {
        creatingRef.current = false;
        console.error('Failed to create workflow:', error);
        showToast('error', t('workflow.messages.createFailed', '创建失败'), '');
      });
    }
    
    return () => {
      resetEditor();
      creatingRef.current = false;
    };
  }, [id, navigate, loadWorkflow, createWorkflow, resetEditor, showToast, t]);

  const handleBack = () => {
    if (isDirty) {
      const confirmed = window.confirm(t('workflow.messages.unsavedChanges', '有未保存的更改，确定要离开吗？'));
      if (!confirmed) return;
    }
    navigate('/ai/workflows');
  };

  // Creation refreshes the list before navigating to the real ID. Keep that
  // interim draft and the previous route's workflow out of editable controls.
  if (id === 'new' || isLoadingCurrentWorkflow || currentWorkflow?.id !== id || !currentWorkflow) {
    return <div className="workflow-editor flex min-w-0 flex-1 flex-col bg-[#f8fafc] p-8 dark:bg-gray-950">
      <button type="button" onClick={handleBack} className="mb-6 flex w-fit items-center gap-2 text-sm text-gray-500 hover:text-purple-600">
        <ChevronLeft className="h-4 w-4" />{t('common.back', '返回')}
      </button>
      <div role={currentWorkflowError ? 'alert' : 'status'} className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-gray-500 dark:text-gray-400">
        {currentWorkflowError ? <>
          <span>{currentWorkflowError}</span>
          {id && id !== 'new' && <button type="button" onClick={() => void loadWorkflow(id)} className="rounded-xl bg-purple-600 px-4 py-2 text-white">
            {t('common.retry', '重试')}
          </button>}
        </> : <><Loader2 className="h-5 w-5 animate-spin text-purple-500" />{t('common.loading', '加载中...')}</>}
      </div>
    </div>;
  }

  return (
    <div className="workflow-editor min-w-0 flex-1 flex flex-col h-full bg-[#f8fafc] dark:bg-gray-950 overflow-hidden">
      {/* Header */}
      <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-8 bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-b border-gray-200/50 dark:border-gray-800/50 flex-shrink-0 z-30 shadow-sm">
        <div className="flex min-w-0 items-center gap-3 md:gap-6">
          <button
            onClick={handleBack}
            className="flex items-center gap-3 text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 transition-all group"
          >
            <div className="p-2 rounded-xl group-hover:bg-gray-100 dark:group-hover:bg-gray-800 transition-colors border border-transparent group-hover:border-gray-200/50 dark:group-hover:border-gray-700/50">
              <ChevronLeft className="w-5 h-5" />
            </div>
            <span className="hidden text-sm font-bold sm:inline">{t('common.back', '返回')}</span>
          </button>

          <div className="h-8 w-px bg-gray-200/50 dark:bg-gray-800" />

          {isLoadingCurrentWorkflow ? (
            <div className="flex items-center gap-2 text-gray-400">
              <Loader2 className="w-4 h-4 animate-spin text-purple-500" />
              <span className="text-sm font-bold tracking-wider">{t('common.loading', '加载中...')}</span>
            </div>
          ) : currentWorkflow ? (
            <div className="flex min-w-0 items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-purple-500 to-indigo-600 flex items-center justify-center text-white shadow-lg shadow-purple-200 dark:shadow-none border border-white/20">
                <GitBranch className="w-6 h-6" />
              </div>
              <div className="flex min-w-0 flex-col">
                <input
                  type="text"
                  value={currentWorkflow.name}
                  onChange={(e) => updateWorkflowMeta({ name: e.target.value })}
                  aria-label={t('businessScenarios.workflow.name')}
                  className="w-44 sm:w-60 text-lg font-bold text-gray-900 dark:text-gray-100 bg-transparent border-none focus:outline-none focus:ring-0 p-0 h-7 min-w-0 hover:bg-gray-50 dark:hover:bg-gray-800/50 rounded-xl transition-all px-2 -ml-2 focus:bg-gray-50 dark:focus:bg-gray-800/50"
                  placeholder={t('workflow.namePlaceholder', '未命名工作流')}
                />
                <div className="flex items-center gap-2 mt-0.5">
                  <span className={`text-[10px] uppercase font-bold tracking-widest px-2 py-0.5 rounded-lg border shadow-sm ${
                    currentWorkflow.status === 'active' 
                      ? 'bg-green-50 text-green-600 border-green-100 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800' 
                      : 'bg-yellow-50 text-yellow-600 border-yellow-100 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800'
                  }`}>
                    {currentWorkflow.status === 'active' ? t('workflow.status.active', 'Active') : t('workflow.status.draft', 'Draft')}
                  </span>
                  {isDirty && (
                    <>
                      <span className="text-gray-300 dark:text-gray-600 font-bold">•</span>
                      <span className="text-[10px] text-amber-500 font-bold uppercase tracking-widest animate-pulse flex items-center gap-1.5">
                        <div className="w-2 h-2 rounded-full bg-amber-500 shadow-sm shadow-amber-200"></div>
                        {t('common.unsaved_changes', '未保存更改')}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="flex max-w-full items-center gap-3 overflow-x-auto pb-1">
          {/* Action Toolbar (Undo, Redo, Validate, Save) */}
          <div className="flex shrink-0 rounded-xl bg-gray-100 p-1 dark:bg-gray-800">
            {(['basic', 'advanced'] as const).map(mode => <button key={mode} onClick={() => { setEditorMode(mode); setSelectedNode(null); }} aria-pressed={editorMode === mode} className={`rounded-lg px-3 py-2 text-xs ${editorMode === mode ? 'bg-white text-purple-600 shadow-sm dark:bg-gray-700' : 'text-gray-500'}`}>{t(`businessScenarios.workflow.${mode}`)}</button>)}
          </div>
          <WorkflowToolbar />

          <div className="h-8 w-px shrink-0 bg-gray-200/50 dark:bg-gray-800" />

          {/* Settings Toggle */}
          <button
            onClick={() => {
              if (!showSettings) {
                setDebugPanelOpen(false);
                setSelectedNode(null);
              }
              setShowSettings(!showSettings);
            }}
            className={`flex items-center gap-2 px-5 h-10 rounded-2xl transition-all border font-bold text-xs active:scale-95 shadow-sm ${
              showSettings
                ? 'bg-gray-900 text-white border-gray-900 dark:bg-white dark:text-gray-900 dark:border-white shadow-lg'
                : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800'
            }`}
            title={t('workflow.settings', '设置')}
          >
            <Settings className={`w-4 h-4 ${showSettings ? 'animate-spin-slow' : ''}`} />
            <span className="hidden sm:inline">{t('common.settings', '设置')}</span>
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="workflow-editor-body min-w-0 flex-1 flex overflow-hidden relative">
        {/* Left Side: Node Palette */}
        {editorMode === 'advanced' && <NodePalette
          isCollapsed={paletteCollapsed} 
          onToggleCollapse={() => setPaletteCollapsed(!paletteCollapsed)} 
        />}

        {/* Center: Editor */}
        <div className="workflow-canvas-area min-w-0 flex-1 relative overflow-hidden z-10">
          {/* Background pattern - now correctly scoped */}
          <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] bg-fixed opacity-[0.03] dark:opacity-[0.01] pointer-events-none z-0" />
          
          {isLoadingCurrentWorkflow ? (
            <div className="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-gray-900/50 backdrop-blur-sm z-20">
              <div className="text-center">
                <Loader2 className="w-10 h-10 animate-spin text-purple-600 mx-auto mb-4" />
                <p className="text-gray-500 dark:text-gray-400 text-sm font-bold uppercase tracking-widest">
                  {t('workflow.loading', '正在加载工作流...')}
                </p>
              </div>
            </div>
          ) : (
            editorMode === 'basic' ? <WorkflowBasicConfiguration onAdvanced={() => setEditorMode('advanced')} /> : <WorkflowEditor />
          )}
        </div>

        {/* Right Side: Panels (Overlay or Fixed) */}
        {isDebugPanelOpen ? (
          <DebugPanel />
        ) : selectedNode ? (
          <NodeConfigPanel node={selectedNode} />
        ) : showSettings && currentWorkflow ? (
          <div className="w-80 bg-white dark:bg-gray-900 border-l border-gray-200/50 dark:border-gray-800/50 flex flex-col shadow-2xl z-30 transition-all animate-in slide-in-from-right duration-300">
            <div className="px-6 py-5 border-b border-gray-100/50 dark:border-gray-800/50 flex items-center justify-between bg-white/80 dark:bg-gray-900/80 backdrop-blur-md sticky top-0 z-10">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-purple-50 dark:bg-purple-900/20 rounded-xl text-purple-600 dark:text-purple-400">
                  <Settings className="w-5 h-5" />
                </div>
                <h3 className="font-bold text-gray-900 dark:text-gray-100 text-sm uppercase tracking-wider">
                  {t('workflow.settings', '工作流设置')}
                </h3>
              </div>
              <button 
                onClick={() => setShowSettings(false)}
                className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors text-gray-400"
              >
                <ChevronRight className="w-5 h-5" />
              </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-6 space-y-8 custom-scrollbar">
              {/* Name */}
              <div className="space-y-2.5">
                <label className="text-[11px] uppercase font-bold text-gray-400 tracking-widest flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-purple-500"></div>
                  {t('workflow.fields.name', '名称')}
                </label>
                <input
                  type="text"
                  value={currentWorkflow.name}
                  onChange={(e) => updateWorkflowMeta({ name: e.target.value })}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 outline-none transition-all text-sm font-bold dark:text-gray-100"
                />
              </div>

              {/* Description */}
              <div className="space-y-2.5">
                <label className="text-[11px] uppercase font-bold text-gray-400 tracking-widest flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-500"></div>
                  {t('workflow.fields.description', '描述')}
                </label>
                <textarea
                  value={currentWorkflow.description || ''}
                  onChange={(e) => updateWorkflowMeta({ description: e.target.value })}
                  rows={4}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 outline-none transition-all text-sm dark:text-gray-100 resize-none leading-relaxed"
                  placeholder={t('workflow.fields.description_placeholder', '描述此工作流的功能...')}
                />
              </div>

              {/* Status */}
              <div className="space-y-2.5">
                <label className="text-[11px] uppercase font-bold text-gray-400 tracking-widest flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-yellow-500"></div>
                  {t('workflow.fields.status', '状态')}
                </label>
                <div className="grid grid-cols-3 gap-2 p-1 bg-gray-50 dark:bg-gray-800/50 rounded-2xl border border-gray-100 dark:border-gray-700">
                  {(['draft', 'active', 'archived'] as const).map((status) => (
                    <button
                      key={status}
                      onClick={() => updateWorkflowMeta({ status })}
                      className={`
                        py-2 text-[10px] font-bold uppercase tracking-widest rounded-xl transition-all
                        ${currentWorkflow.status === status
                          ? 'bg-white dark:bg-gray-700 text-purple-600 dark:text-purple-400 shadow-sm border border-gray-100 dark:border-gray-600'
                          : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
                        }
                      `}
                    >
                      {status === 'active' ? t('workflow.status.active', 'Active') : 
                       status === 'draft' ? t('workflow.status.draft', 'Draft') : 
                       t('workflow.status.archived', 'Archived')}
                    </button>
                  ))}
                </div>
              </div>

              {/* Tags */}
              <div className="space-y-2.5">
                <label className="text-[11px] uppercase font-bold text-gray-400 tracking-widest flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-500"></div>
                  {t('workflow.fields.tags', '标签')}
                </label>
                <input
                  type="text"
                  value={currentWorkflow.tags.join(', ')}
                  onChange={(e) => updateWorkflowMeta({ 
                    tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean)
                  })}
                  placeholder={t('workflow.tagsPlaceholder', '用逗号分隔')}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 outline-none transition-all text-sm dark:text-gray-100"
                />
              </div>

              {/* Info Box */}
              <div className="p-6 bg-gradient-to-br from-purple-50 to-indigo-50 dark:from-purple-900/20 dark:to-indigo-900/20 rounded-3xl border border-purple-100 dark:border-purple-800/50 shadow-sm">
                <div className="flex items-center gap-2 mb-4">
                  <Info className="w-4 h-4 text-purple-600 dark:text-purple-400" />
                  <span className="text-[11px] font-bold text-purple-700 dark:text-purple-300 uppercase tracking-widest">{t('workflow.fields.workflow_info', '工作流详情')}</span>
                </div>
                <div className="space-y-4">
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] text-gray-500 dark:text-gray-400 font-bold uppercase tracking-wider">{t('workflow.fields.version', '版本')}</span>
                    <span className="text-xs font-mono font-bold text-gray-900 dark:text-gray-100 bg-white/50 dark:bg-gray-800/50 px-2 py-0.5 rounded-lg border border-purple-100/50 dark:border-purple-700/50">v{currentWorkflow.version}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] text-gray-500 dark:text-gray-400 font-bold uppercase tracking-wider">{t('workflow.fields.total_nodes', '节点总数')}</span>
                    <span className="text-xs font-mono font-bold text-gray-900 dark:text-gray-100 bg-white/50 dark:bg-gray-800/50 px-2 py-0.5 rounded-lg border border-purple-100/50 dark:border-purple-700/50">{currentWorkflow.definition.nodes.length}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] text-gray-500 dark:text-gray-400 font-bold uppercase tracking-wider">{t('common.tableCreated', '创建于')}</span>
                    <span className="text-[10px] font-mono font-bold text-gray-700 dark:text-gray-300">{new Date(currentWorkflow.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default WorkflowEditorPage;
