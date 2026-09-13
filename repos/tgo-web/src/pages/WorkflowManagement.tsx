/**
 * Workflow Management Page
 * List and manage AI Agent workflows
 */

import React, { useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  Plus,
  GitBranch,
  MoreVertical,
  Pencil,
  Copy,
  Trash2,
  RefreshCw,
  Eye,
  Search,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useToast } from '@/hooks/useToast';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import ConfirmDialog from '@/components/ui/ConfirmDialog';
import type { WorkflowSummary, WorkflowStatus } from '@/types/workflow';

/**
 * Workflow Card Component
 */
interface WorkflowCardProps {
  workflow: WorkflowSummary;
  onEdit: (id: string) => void;
  onDuplicate: (id: string) => void;
  onDelete: (id: string) => void;
}

const WorkflowCard: React.FC<WorkflowCardProps> = ({
  workflow,
  onEdit,
  onDuplicate,
  onDelete,
}) => {
  const { t } = useTranslation();
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false);
      }
    };
    if (showMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    } else {
      document.removeEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showMenu]);

  const getStatusIndicator = (status: WorkflowStatus) => {
    switch (status) {
      case 'active':
        return { 
          color: 'bg-green-500', 
          textColor: 'text-green-600 dark:text-green-400',
          bgColor: 'bg-green-50 dark:bg-green-900/20',
          borderColor: 'border-green-200 dark:border-green-800',
          title: t('workflow.status.active', '已启用') 
        };
      case 'draft':
        return { 
          color: 'bg-yellow-500', 
          textColor: 'text-yellow-600 dark:text-yellow-400',
          bgColor: 'bg-yellow-50 dark:bg-yellow-900/20',
          borderColor: 'border-yellow-200 dark:border-yellow-800',
          title: t('workflow.status.draft', '草稿') 
        };
      case 'archived':
        return { 
          color: 'bg-gray-400', 
          textColor: 'text-gray-500 dark:text-gray-400',
          bgColor: 'bg-gray-50 dark:bg-gray-700/30',
          borderColor: 'border-gray-200 dark:border-gray-700',
          title: t('workflow.status.archived', '已归档') 
        };
      default:
        return { 
          color: 'bg-gray-400', 
          textColor: 'text-gray-500 dark:text-gray-400',
          bgColor: 'bg-gray-50 dark:bg-gray-700/30',
          borderColor: 'border-gray-200 dark:border-gray-700',
          title: status 
        };
    }
  };

  const status = getStatusIndicator(workflow.status);

  return (
    <div className="group relative bg-white dark:bg-gray-800 rounded-2xl p-5 flex flex-col justify-between shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300 border border-gray-100 dark:border-gray-700 overflow-hidden">
      {/* Background decoration */}
      <div className={`absolute -right-8 -top-8 w-24 h-24 rounded-full opacity-[0.03] dark:opacity-[0.05] ${status.color}`}></div>
      
      <div>
        <div className="flex justify-between items-start mb-4">
          <div className="flex items-center space-x-4">
            <div className="relative group/icon">
              <div className={`absolute -inset-1 rounded-xl opacity-20 group-hover/icon:opacity-40 transition-opacity duration-300 ${status.color}`}></div>
              <div className="relative w-12 h-12 rounded-xl bg-gradient-to-br from-purple-500 to-indigo-600 flex items-center justify-center text-white border-2 border-white dark:border-gray-700 shadow-sm">
                <GitBranch className="w-6 h-6" />
              </div>
              <div className={`absolute -bottom-1 -right-1 w-3.5 h-3.5 rounded-full border-2 border-white dark:border-gray-800 ${status.color}`}></div>
            </div>
            <div className="min-w-0">
              <h3 className="font-bold text-gray-900 dark:text-gray-100 group-hover:text-purple-600 dark:group-hover:text-purple-400 transition-colors duration-200 truncate pr-2" title={workflow.name}>
                {workflow.name}
              </h3>
              <div className="flex items-center mt-0.5">
                <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium border ${status.bgColor} ${status.textColor} ${status.borderColor}`}>
                  {status.title}
                </span>
                <span className="mx-1.5 text-gray-300 dark:text-gray-600">•</span>
                <span className="text-xs text-gray-500 dark:text-gray-400 font-medium">v{workflow.version}</span>
              </div>
            </div>
          </div>
          
          <div className="relative" ref={menuRef}>
            <button 
              onClick={() => setShowMenu(!showMenu)}
              className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 transition-colors"
            >
              <MoreVertical className="w-5 h-5" />
            </button>
            
            {showMenu && (
              <div className="absolute right-0 mt-2 w-40 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-100 dark:border-gray-700 py-1 z-20 animate-in fade-in zoom-in-95 duration-200">
                <button onClick={() => { onEdit(workflow.id); setShowMenu(false); }} className="w-full flex items-center px-3 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <Pencil className="w-4 h-4 mr-2" /> {t('common.edit', '编辑')}
                </button>
                <button onClick={() => { onDuplicate(workflow.id); setShowMenu(false); }} className="w-full flex items-center px-3 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <Copy className="w-4 h-4 mr-2" /> {t('common.duplicate', '复制')}
                </button>
                <div className="h-px bg-gray-100 dark:bg-gray-700 my-1"></div>
                <button onClick={() => { onDelete(workflow.id); setShowMenu(false); }} className="w-full flex items-center px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                  <Trash2 className="w-4 h-4 mr-2" /> {t('common.delete', '删除')}
                </button>
              </div>
            )}
          </div>
        </div>

        <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed mb-4 line-clamp-2 h-10 group-hover:text-gray-700 dark:group-hover:text-gray-300 transition-colors">
          {workflow.description || t('workflow.noDescription', '暂无描述信息')}
        </p>

        <div className="space-y-3">
          {/* Tags */}
          {workflow.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 min-h-[24px]">
              {workflow.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 text-[10px] bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 rounded-md font-medium border border-purple-100 dark:border-purple-800"
                >
                  {tag}
                </span>
              ))}
              {workflow.tags.length > 3 && (
                <span className="text-[10px] text-gray-400 font-medium self-center">
                  +{workflow.tags.length - 3}
                </span>
              )}
            </div>
          )}
          
          <div className="flex items-center text-[11px] text-gray-500 dark:text-gray-500 bg-gray-50 dark:bg-gray-800/50 px-2 py-1.5 rounded-lg border border-gray-100/50 dark:border-gray-700/50">
            <RefreshCw className="w-3.5 h-3.5 mr-2 opacity-70" />
            <span>{t('workflow.lastUpdated', '更新于')} {new Date(workflow.updated_at).toLocaleDateString()}</span>
          </div>
        </div>
      </div>

      <div className="mt-5 flex items-center gap-2">
        <button
          onClick={() => onEdit(workflow.id)}
          className="flex-1 flex items-center justify-center gap-2 py-2 px-4 bg-purple-600 hover:bg-purple-700 text-white text-xs font-bold rounded-xl shadow-lg shadow-purple-200 dark:shadow-none transition-all duration-200 active:scale-95"
        >
          <Eye className="w-3.5 h-3.5" />
          {t('workflow.viewWorkflow', '查看工作流')}
        </button>
        <button
          onClick={() => onEdit(workflow.id)}
          className="p-2 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-300 hover:text-purple-600 dark:hover:text-purple-400 rounded-xl border border-gray-100 dark:border-gray-600 transition-all duration-200 hover:bg-purple-50 dark:hover:bg-purple-900/30"
          title={t('common.edit', '编辑')}
        >
          <Pencil className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

/**
 * Workflow Management Page Component
 */
const WorkflowManagement: React.FC = () => {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const navigate = useNavigate();
  
  const {
    workflows,
    isLoadingWorkflows,
    workflowsError,
    loadWorkflows,
    createWorkflow,
    deleteWorkflow,
    duplicateWorkflow,
  } = useWorkflowStore();

  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebouncedValue(searchQuery, 300);
  const [statusFilter, setStatusFilter] = useState<WorkflowStatus | 'all'>('all');

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<WorkflowSummary | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Load workflows on mount
  useEffect(() => {
    loadWorkflows();
  }, [loadWorkflows]);

  const [isCreating, setIsCreating] = useState(false);

  // Filter workflows
  const { filteredWorkflows, totalPages, currentPage } = React.useMemo(() => {
    let filtered = workflows;

    // Status filter
    if (statusFilter !== 'all') {
      filtered = filtered.filter(wf => wf.status === statusFilter);
    }

    // Search filter
    if (debouncedSearch.trim()) {
      const query = debouncedSearch.toLowerCase();
      filtered = filtered.filter(wf =>
        wf.name.toLowerCase().includes(query) ||
        (wf.description && wf.description.toLowerCase().includes(query))
      );
    }

    // For now, simple pagination matching AgentManagement
    const pageSize = 12;
    const total = Math.ceil(filtered.length / pageSize);
    const paginated = filtered.slice(0, pageSize); // Simplified pagination for now

    return { 
      filteredWorkflows: filtered, 
      paginatedWorkflows: paginated, 
      totalPages: total, 
      currentPage: 1 
    };
  }, [workflows, statusFilter, debouncedSearch]);

  // Handlers
  const handleCreate = async () => {
    if (isCreating) return;
    setIsCreating(true);
    try {
      const newWorkflow = await createWorkflow();
      navigate(`/ai/workflows/${newWorkflow.id}/edit`);
    } catch (error) {
      console.error('Failed to create workflow:', error);
      showToast('error', t('workflow.messages.createFailed', '创建失败'), '');
    } finally {
      setIsCreating(false);
    }
  };

  const handleEdit = (id: string) => {
    navigate(`/ai/workflows/${id}/edit`);
  };

  const handleDuplicate = async (id: string) => {
    try {
      await duplicateWorkflow(id);
      showToast('success', t('workflow.messages.duplicateSuccess', '复制成功'), '');
    } catch {
      showToast('error', t('workflow.messages.duplicateFailed', '复制失败'), '');
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    try {
      await deleteWorkflow(deleteTarget.id);
      showToast('success', t('workflow.messages.deleteSuccess', '删除成功'), '');
      setDeleteTarget(null);
    } catch {
      showToast('error', t('workflow.messages.deleteFailed', '删除失败'), '');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleRefresh = () => {
    loadWorkflows();
  };

  return (
    <main className="flex-grow flex flex-col bg-[#f8fafc] dark:bg-gray-950 overflow-hidden">
      {/* Header */}
      <header className="px-8 py-5 bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col md:flex-row md:items-center justify-between gap-4 sticky top-0 z-30">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <GitBranch className="w-7 h-7 text-purple-600" />
            {t('workflow.management.title', '工作流管理')}
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {t('workflow.management.subtitle', '编排复杂的 AI 任务流程')}
          </p>
        </div>
        
        <div className="flex items-center gap-3">
          <div className="relative group hidden sm:block">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 group-focus-within:text-purple-500 transition-colors" />
            <input 
              type="text"
              placeholder={t('common.search', '搜索...')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10 pr-4 py-2 w-64 bg-gray-100/50 dark:bg-gray-800/50 border-transparent focus:bg-white dark:focus:bg-gray-800 focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 rounded-xl text-sm transition-all outline-none"
            />
          </div>
          
          <div className="h-8 w-px bg-gray-200 dark:border-gray-800 mx-1 hidden sm:block"></div>
          
          <div className="flex items-center gap-2">
            <button
              className="p-2 text-gray-500 hover:text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 rounded-xl transition-all"
              onClick={handleRefresh}
              disabled={isLoadingWorkflows}
              title={t('common.refresh', '刷新')}
            >
              <RefreshCw className={`w-5 h-5 ${isLoadingWorkflows ? 'animate-spin' : ''}`} />
            </button>
            <button
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white text-sm font-bold rounded-xl shadow-lg shadow-purple-200 dark:shadow-none transition-all active:scale-95"
              onClick={handleCreate}
              disabled={isCreating}
            >
              {isCreating ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              <span className="hidden sm:inline">{t('workflow.actions.create', '创建工作流')}</span>
            </button>
          </div>
        </div>
      </header>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="max-w-[1600px] mx-auto p-8 space-y-8">
          
          {/* Quick Actions / Info Banner */}
          <div className="bg-gradient-to-r from-purple-600 to-indigo-700 rounded-3xl p-6 text-white shadow-xl shadow-purple-200 dark:shadow-none flex flex-col md:flex-row items-center justify-between gap-6 relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] opacity-10"></div>
            <div className="relative z-10">
              <h3 className="text-xl font-bold flex items-center gap-2">
                <GitBranch className="w-6 h-6" />
                {t('workflow.banner.title', '自动化工作流')}
              </h3>
              <p className="text-purple-100 text-sm mt-1 opacity-90 max-w-xl">
                {t('workflow.banner.description', '通过图形化界面编排 AI 节点、条件分支和自定义工具，实现从简单对话到复杂业务逻辑的自动化处理。')}
              </p>
            </div>
            <div className="flex items-center gap-2 relative z-10">
              {(['all', 'active', 'draft', 'archived'] as const).map((status) => (
                <button
                  key={status}
                  onClick={() => setStatusFilter(status)}
                  className={`px-4 py-2 rounded-xl text-sm font-bold transition-all backdrop-blur-md border ${
                    statusFilter === status
                      ? 'bg-white text-purple-600 border-white shadow-lg'
                      : 'bg-white/10 text-white border-white/20 hover:bg-white/20'
                  }`}
                >
                  {status === 'all' ? t('common.all', '全部') :
                   status === 'active' ? t('workflow.status.active', '已启用') :
                   status === 'draft' ? t('workflow.status.draft', '草稿') :
                   t('workflow.status.archived', '已归档')}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('workflow.listTitle', '工作流列表')}</h3>
            </div>

            {/* Error State */}
            {workflowsError ? (
              <div className="flex flex-col items-center justify-center py-12 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100 dark:border-gray-800 shadow-sm">
                <div className="w-16 h-16 bg-red-50 dark:bg-red-900/20 rounded-2xl flex items-center justify-center text-red-500 mb-4">
                  <Trash2 className="w-8 h-8" />
                </div>
                <p className="text-gray-900 dark:text-gray-100 font-bold mb-2">{t('common.error', '出错了')}</p>
                <p className="text-gray-500 dark:text-gray-400 text-sm mb-6">{workflowsError}</p>
                <button
                  onClick={handleRefresh}
                  className="px-6 py-2 bg-purple-600 hover:bg-purple-700 text-white font-bold rounded-xl transition-all"
                >
                  {t('common.retry', '重试')}
                </button>
              </div>
            ) : isLoadingWorkflows && workflows.length === 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                {[...Array(8)].map((_, i) => (
                  <div key={i} className="h-64 bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 animate-pulse" />
                ))}
              </div>
            ) : filteredWorkflows.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100 dark:border-gray-800 shadow-sm">
                <div className="w-20 h-20 bg-gray-50 dark:bg-gray-800 rounded-3xl flex items-center justify-center text-gray-300 dark:text-gray-600 mb-6">
                  <GitBranch className="w-10 h-10" />
                </div>
                <h3 className="text-xl font-bold text-gray-900 dark:text-gray-100 mb-2">
                  {searchQuery || statusFilter !== 'all' ? t('workflow.empty.noResults', '未找到匹配的工作流') : t('workflow.empty.title', '暂无工作流')}
                </h3>
                <p className="text-gray-500 dark:text-gray-400 mb-8 max-w-sm text-center">
                  {searchQuery || statusFilter !== 'all' ? t('workflow.empty.noResultsDesc', '请尝试调整搜索关键词或筛选条件') : t('workflow.empty.description', '点击「创建工作流」按钮开始构建您的第一个自动化流程')}
                </p>
                {!searchQuery && statusFilter === 'all' && (
                  <button
                    onClick={handleCreate}
                    className="inline-flex items-center px-6 py-3 bg-purple-600 hover:bg-purple-700 text-white font-bold rounded-xl shadow-lg shadow-purple-200 dark:shadow-none transition-all active:scale-95"
                  >
                    <Plus className="w-5 h-5 mr-2" />
                    {t('workflow.actions.create', '创建工作流')}
                  </button>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                {filteredWorkflows.map((workflow) => (
                  <WorkflowCard
                    key={workflow.id}
                    workflow={workflow}
                    onEdit={handleEdit}
                    onDuplicate={handleDuplicate}
                    onDelete={(id) => setDeleteTarget(workflows.find(w => w.id === id) || null)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Pagination Placeholder matching AgentManagement style */}
          {totalPages > 1 && (
            <div className="flex justify-center pt-4 pb-12">
              <nav className="flex items-center gap-1 p-1.5 bg-white dark:bg-gray-900 border border-gray-200/50 dark:border-gray-800 rounded-2xl shadow-sm">
                <button className="p-2 rounded-xl text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-30 transition-all">
                  <ChevronLeft className="w-5 h-5" />
                </button>
                <div className="flex items-center gap-1 px-2">
                  {[...Array(totalPages)].map((_, i) => (
                    <button
                      key={i}
                      className={`min-w-[36px] h-9 flex items-center justify-center rounded-xl text-sm font-bold transition-all ${
                        i + 1 === currentPage
                          ? 'bg-purple-600 text-white shadow-md shadow-purple-200 dark:shadow-none scale-105'
                          : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800'
                      }`}
                    >
                      {i + 1}
                    </button>
                  ))}
                </div>
                <button className="p-2 rounded-xl text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-30 transition-all">
                  <ChevronRight className="w-5 h-5" />
                </button>
              </nav>
            </div>
          )}
        </div>
      </div>

      {/* Delete Confirmation */}
      <ConfirmDialog
        isOpen={!!deleteTarget}
        title={t('workflow.modal.delete.title', '删除工作流')}
        message={t('workflow.modal.delete.message', `确定要删除工作流 "${deleteTarget?.name}" 吗？此操作不可撤销。`, { name: deleteTarget?.name })}
        confirmText={t('common.delete', '删除')}
        cancelText={t('common.cancel', '取消')}
        confirmVariant="danger"
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
        isLoading={isDeleting}
      />
    </main>
  );
};

export default WorkflowManagement;
