import React, { useState, useMemo, useEffect, useRef } from 'react';
import { LuPackage, LuSearch } from 'react-icons/lu';
import { Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ToolsList from './ToolsList';
import AddToolModal from './AddToolModal';
import EditToolModal from './EditToolModal';
import AddHTTPToolModal from './AddHTTPToolModal';
import EditHTTPToolModal from './EditHTTPToolModal';
import { ToolToastProvider, useToast } from './ToolToastProvider';
import ConfirmDialog from '@/components/ui/ConfirmDialog';
import { useProjectToolsStore } from '@/stores/projectToolsStore';
import { transformAiToolResponseList, searchProjectTools } from '@/utils/projectToolsTransform';
import { AiToolsGridSkeleton, AiToolsErrorState, AiToolsEmptyState } from '@/components/ui/AiToolsSkeleton';
import SavedToolTestModal from './SavedToolTestModal';
import LogisticsProviderModal from './LogisticsProviderModal';
import { getLogisticsProviderTool, isLogisticsProviderTool } from '@/services/logisticsProviderService';
import type { AiTool, AiToolResponse } from '@/types';

/**
 * Tools page component content
 * Updated to use NEW /v1/ai/tools API
 */
const ToolsContent: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [showAddToolModal, setShowAddToolModal] = useState<boolean>(false);
  const [showAddHTTPToolModal, setShowAddHTTPToolModal] = useState<boolean>(false);
  const [showEditToolModal, setShowEditToolModal] = useState<boolean>(false);
  const [showEditHTTPToolModal, setShowEditHTTPToolModal] = useState<boolean>(false);
  const [testTool, setTestTool] = useState<AiToolResponse | null>(null);
  const [providerTool, setProviderTool] = useState<AiToolResponse | null | undefined>(undefined);
  const [selectedToolForEdit, setSelectedToolForEdit] = useState<AiToolResponse | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<boolean>(false);
  const [toolToDelete, setToolToDelete] = useState<AiTool | null>(null);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const deletingRef = useRef(false);
  const { showToast } = useToast();
  const { t } = useTranslation();

  // Project Tools store (now using AI tools)
  const {
    aiTools,
    isLoading,
    error,
    loadTools,
    deleteTool,
    clearError,
  } = useProjectToolsStore();

  // Load tools on component mount (only active tools by default)
  useEffect(() => {
    loadTools(false); // false = don't include deleted tools
  }, [loadTools]);

  // Transform API AI tools to component format
  const tools = useMemo(() => {
    return transformAiToolResponseList(aiTools);
  }, [aiTools]);

  // Filter tools by search query (client-side for better UX)
  const filteredTools = useMemo(() => {
    let filtered = tools;

    // Apply search filter
    if (searchQuery.trim()) {
      filtered = searchProjectTools(filtered, searchQuery);
    }

    return filtered;
  }, [tools, searchQuery]);

  const handleToolAction = async (actionType: string, tool: AiTool): Promise<void> => {
    try {
      switch (actionType) {
        case 'test':
          const logisticsTool = aiTools.find(item => item.id === tool.id);
          if (logisticsTool && isLogisticsProviderTool(logisticsTool)) {
            setProviderTool(await getLogisticsProviderTool());
            break;
          }
          setTestTool(aiTools.find(item => item.id === tool.id) ?? null);
          break;
        case 'delete':
        case 'uninstall':
          setToolToDelete(tool);
          setShowDeleteConfirm(true);
          break;
        case 'edit':
          // Find the original AiToolResponse for editing
          const aiTool = aiTools.find(t => t.id === tool.id);
          if (aiTool) {
            if (isLogisticsProviderTool(aiTool)) {
              setProviderTool(await getLogisticsProviderTool());
              break;
            }
            setSelectedToolForEdit(aiTool);
            if (aiTool.transport_type === 'http_webhook') {
              setShowEditHTTPToolModal(true);
            } else {
              setShowEditToolModal(true);
            }
          } else {
            showToast('error', t('tools.tools.edit.notFound', '工具未找到'), t('tools.tools.edit.notFoundMessage', '无法找到该工具的详细信息'));
          }
          break;
        default:
          showToast('warning', t('tools.tools.unknownAction.title', '未知操作'), t('tools.tools.unknownAction.message', { type: actionType, defaultValue: `不支持的操作类型: ${actionType}` }));
      }
    } catch (error) {
      console.error(`Tool action ${actionType} failed:`, error);
      const errorMessage = error instanceof Error ? error.message : t('tools.tools.actionFailed.title', '操作失败');
      showToast('error', t('tools.tools.actionFailed.title', '操作失败'), errorMessage);
    }
  };

  // Handle retry on error
  const handleRetry = () => {
    clearError();
    loadTools(false);
  };



  const handleSearchChange = (query: string): void => {
    setSearchQuery(query);
  };

  const handleDeleteToolConfirm = async () => {
    if (!toolToDelete || deletingRef.current) return;
    deletingRef.current = true;
    setIsDeleting(true);
    try {
      await deleteTool(toolToDelete.id);
      showToast('success', t('tools.tools.delete.successTitle', '删除成功'), t('tools.tools.delete.successMessage', { name: toolToDelete.name, defaultValue: `工具 "${toolToDelete.name}" 已被删除` }));
      await loadTools(false);
      setShowDeleteConfirm(false);
      setToolToDelete(null);
    } catch (error) {
      console.error('Failed to delete tool:', error);
      const errorMessage = error instanceof Error ? error.message : t('tools.tools.actionFailed.title', '操作失败');
      showToast('error', t('tools.tools.actionFailed.title', '操作失败'), errorMessage);
    } finally {
      deletingRef.current = false;
      setIsDeleting(false);
    }
  };

  return (
    <main className="flex-grow flex flex-col bg-[#f8fafc] dark:bg-gray-950 overflow-hidden">
      {/* Header */}
      <header className="px-8 py-5 bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col xl:flex-row xl:items-center justify-between gap-4 sticky top-0 z-30">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <LuPackage className="w-7 h-7 text-blue-600" />
            {t('tools.title', '工具管理')}
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {t('tools.probe.savedHint', '接入业务工具；保存后点击「测试工具」，确认可用再绑定给 AI 员工。')}
          </p>
        </div>
        
        <div className="flex flex-wrap items-center gap-3">
          {/* Search Bar matching AgentManagement */}
          <div className="relative group hidden sm:block">
            <LuSearch className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 group-focus-within:text-blue-500 transition-colors" />
            <input 
              type="text"
              placeholder={t('tools.search.placeholder', '搜索工具...')}
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="pl-10 pr-4 py-2 w-48 lg:w-64 bg-gray-100/50 dark:bg-gray-800/50 border-transparent focus:bg-white dark:focus:bg-gray-800 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 rounded-xl text-sm transition-all outline-none"
            />
          </div>

          <div className="h-8 w-px bg-gray-200 dark:bg-gray-800 mx-1 hidden sm:block"></div>

          <button className="rounded-xl border border-blue-300 px-4 py-2 text-sm font-bold text-blue-600 dark:text-blue-300" onClick={async () => {
            try {
              setProviderTool(await getLogisticsProviderTool());
            } catch {
              showToast('error', t('common.error'), t('logisticsProvider.loadFailed'));
            }
          }}>{t('logisticsProvider.add')}</button>


          <button
            className="flex items-center gap-2 px-4 py-2 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 text-sm font-bold rounded-xl border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-all active:scale-95 shadow-sm"
            onClick={() => setShowAddHTTPToolModal(true)}
          >
            <Globe className="w-4 h-4 shrink-0" />
            <span className="whitespace-nowrap">{t('tools.addHttpTool', '添加 HTTP 工具')}</span>
          </button>
          <button
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold rounded-xl shadow-lg shadow-blue-200 dark:shadow-none transition-all active:scale-95"
            onClick={() => setShowAddToolModal(true)}
          >
            <LuPackage className="w-4 h-4 shrink-0" />
            <span className="whitespace-nowrap">{t('tools.addMCPTool', '添加 MCP 工具')}</span>
          </button>
        </div>
      </header>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="max-w-[1600px] mx-auto p-8 space-y-8">
          
          {/* Banner / Info Card */}
          <div className="bg-gradient-to-r from-blue-600 to-indigo-700 rounded-3xl p-6 text-white shadow-xl shadow-blue-200 dark:shadow-none flex flex-col md:flex-row items-center justify-between gap-6 relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] opacity-10"></div>
            <div className="relative z-10">
              <h3 className="text-xl font-bold flex items-center gap-2">
                <LuPackage className="w-6 h-6" />
                {t('tools.banner.title', '增强 AI 能力')}
              </h3>
              <p className="text-blue-100 text-sm mt-1 opacity-90 max-w-xl">
                {t('tools.banner.description', '通过添加 HTTP 接口或 MCP 协议工具，您可以让 AI 员工具备搜索、查询数据库、调用外部服务等无限可能。')}
              </p>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">{t('tools.listTitle', '工具列表')}</h3>
            </div>

            {/* Tools List with Loading/Error States */}
            {error ? (
              <AiToolsErrorState
                error={error}
                onRetry={handleRetry}
              />
            ) : isLoading ? (
              <AiToolsGridSkeleton count={8} />
            ) : filteredTools.length === 0 ? (
              <AiToolsEmptyState
                title={t('tools.tools.installedEmpty.title', '暂无已安装的工具')}
                description={t('tools.tools.installedEmpty.description', '点击右上角按钮创建您的第一个工具')}
              />
            ) : (
              <ToolsList
                tools={filteredTools}
                selectedCategory="all"
                onToolAction={handleToolAction}
                onShowToast={showToast}
              />
            )}
          </div>
        </div>
      </div>

      {/* Add MCP Tool Modal */}
      <AddToolModal
        isOpen={showAddToolModal}
        onClose={() => setShowAddToolModal(false)}
      />

      {/* Add HTTP Tool Modal */}
      <AddHTTPToolModal
        isOpen={showAddHTTPToolModal}
        onClose={() => setShowAddHTTPToolModal(false)}
      />

      {/* Edit MCP Tool Modal */}
      <EditToolModal
        isOpen={showEditToolModal}
        onClose={() => {
          setShowEditToolModal(false);
          setSelectedToolForEdit(null);
        }}
        tool={selectedToolForEdit}
      />

      {/* Edit HTTP Tool Modal */}
      <EditHTTPToolModal
        isOpen={showEditHTTPToolModal}
        onClose={() => {
          setShowEditHTTPToolModal(false);
          setSelectedToolForEdit(null);
        }}
        tool={selectedToolForEdit}
      />

      {testTool && <SavedToolTestModal tool={testTool} onClose={() => setTestTool(null)} />}
      {providerTool !== undefined && <LogisticsProviderModal tool={providerTool} onClose={() => setProviderTool(undefined)} onSaved={() => { void loadTools(false); }} />}

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title={t('tools.tools.confirmDelete.title', '确认删除')}
        message={t('tools.tools.confirmDelete.message', { name: toolToDelete?.name, defaultValue: `确定要删除工具 "${toolToDelete?.name}" 吗？此操作无法撤销。` })}
        confirmText={t('common.delete', '删除')}
        cancelText={t('common.cancel', '取消')}
        confirmVariant="danger"
        onConfirm={handleDeleteToolConfirm}
        onCancel={() => {
          setShowDeleteConfirm(false);
          setToolToDelete(null);
        }}
        isLoading={isDeleting}
      />
    </main>
  );
};

/**
 * Tools page component with Toast provider
 */
const Tools: React.FC = () => {
  return (
    <ToolToastProvider>
      <ToolsContent />
    </ToolToastProvider>
  );
};

export default Tools;
