import React, { useState, useMemo, useEffect } from 'react';
import { X, Search, ExternalLink, FolderOpen, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { getIconComponent, getIconColor } from '@/components/knowledge/IconPicker';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useKnowledgeStore } from '@/stores';
import { useToast } from '@/hooks/useToast';
import type { KnowledgeBaseItem } from '@/types';

interface KnowledgeBaseSelectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedKnowledgeBases: string[];
  onConfirm: (selectedKnowledgeBaseIds: string[]) => void;
}

// Categories: only keep "All"
const KB_CATEGORIES = [
  { id: 'all' }
];

// Render KB icon using centralized IconPicker mapping (ensures consistent color)
const KBIcon: React.FC<{ kb: KnowledgeBaseItem; selected?: boolean }> = ({ kb }) => {
  const IconCmp = getIconComponent(kb.icon);
  const color = getIconColor(kb.icon) || 'text-gray-500';
  // Keep the same color scheme as management list; do not override with selection color
  return <IconCmp className={`w-5 h-5 flex-shrink-0 mt-0.5 ${color}`} />;
};

/**
 * Knowledge Base Selection Modal Component
 * Allows users to browse and select knowledge bases to associate with agents
 */
const KnowledgeBaseSelectionModal: React.FC<KnowledgeBaseSelectionModalProps> = ({
  isOpen,
  onClose,
  selectedKnowledgeBases,
  onConfirm
}) => {
  const navigate = useNavigate();
  const {
    knowledgeBases,
    isLoading,
    error,
    fetchKnowledgeBases,
    refreshKnowledgeBases
  } = useKnowledgeStore();
  const { showToast } = useToast();
  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebouncedValue(searchQuery, 300);
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [tempSelectedKnowledgeBases, setTempSelectedKnowledgeBases] = useState<string[]>([]);
  const totalCount = knowledgeBases.length;
  const { t } = useTranslation();

  // Load knowledge bases when modal opens
  useEffect(() => {
    if (isOpen && knowledgeBases.length === 0) {
      fetchKnowledgeBases().catch(error => {
        console.error('Failed to load knowledge bases:', error);
        showToast('error', t('common.loadFailed', '加载失败'), t('knowledge.selectModal.loadFailedDesc', '无法加载知识库列表，请稍后重试'));
      });
    }
  }, [isOpen, knowledgeBases.length, fetchKnowledgeBases, showToast, t]);

  // Initialize temporary selection state when modal opens
  useEffect(() => {
    if (isOpen) {
      setTempSelectedKnowledgeBases([...selectedKnowledgeBases]);
    }
  }, [isOpen, selectedKnowledgeBases]);

  // Filter knowledge bases based on search and category
  const filteredKnowledgeBases = useMemo(() => {
    let filtered = knowledgeBases;

    // Category simplified to 'all' only (no filtering)

    // Filter by search query
    if (debouncedSearch.trim()) {
      const query = debouncedSearch.toLowerCase();
      filtered = filtered.filter(kb =>
        kb.title.toLowerCase().includes(query) ||
        kb.content.toLowerCase().includes(query) ||
        kb.author.toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [knowledgeBases, debouncedSearch]);

  const handleKnowledgeBaseClick = (knowledgeBase: KnowledgeBaseItem) => {
    setTempSelectedKnowledgeBases(prev => {
      if (prev.includes(knowledgeBase.id)) {
        return prev.filter(id => id !== knowledgeBase.id);
      } else {
        return [...prev, knowledgeBase.id];
      }
    });
  };

  const handleConfirm = () => {
    onConfirm(tempSelectedKnowledgeBases);
    onClose();
  };

  const handleCancel = () => {
    // Reset temporary selection to original state
    setTempSelectedKnowledgeBases([...selectedKnowledgeBases]);
    onClose();
  };

  const handleRetry = () => {
    refreshKnowledgeBases().catch(error => {
      console.error('Failed to retry loading knowledge bases:', error);
      showToast('error', t('common.retryFailed', '重试失败'), t('knowledge.selectModal.retryFailedDesc', '无法加载知识库列表，请检查网络连接'));
    });
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{t('knowledge.selectModal.title', '选择知识库')}</h3>
          <button
            onClick={onClose}
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
              placeholder={t('knowledge.selectModal.searchPlaceholder', '搜索知识库...')}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-gray-100 dark:placeholder-gray-400"
            />
          </div>
        </div>

        {/* Category + Actions */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex space-x-2">
              {KB_CATEGORIES.map(category => (
                <button
                  key={category.id}
                  onClick={() => setSelectedCategory(category.id)}
                  className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                    selectedCategory === category.id
                      ? 'bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-700'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600'
                  }`}
                >
                  {t('common.all', '全部')}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <span>{t('knowledge.selectModal.selectedSummary', { selected: tempSelectedKnowledgeBases.length, total: totalCount, defaultValue: `已选 ${tempSelectedKnowledgeBases.length} / 共 ${totalCount}` })}</span>
              <span className="text-gray-300 dark:text-gray-600">|</span>
              <button
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600"
                onClick={() => {
                  // Select all current filtered items
                  const allIds = filteredKnowledgeBases.map(kb => kb.id);
                  setTempSelectedKnowledgeBases(allIds);
                }}
              >
                {t('common.selectAll', '全选')}
              </button>
              <button
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600"
                onClick={() => setTempSelectedKnowledgeBases([])}
              >
                {t('common.clear', '清空')}
              </button>
            </div>
          </div>
        </div>

        {/* Knowledge Bases List - Scrollable Content Area */}
        <div className="flex-1 overflow-y-auto min-h-0 dark:bg-gray-900">
          <div className="p-4">
            {/* Loading State */}
            {isLoading && (
              <div className="flex items-center justify-center py-8">
                <div className="text-center">
                  <RefreshCw className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500 mx-auto mb-2" />
                  <p className="text-gray-500 dark:text-gray-400 text-sm">{t('knowledge.selectModal.loading', '正在加载知识库...')}</p>
                </div>
              </div>
            )}

            {/* Error State */}
            {error && !isLoading && (
              <div className="flex items-center justify-center py-8">
                <div className="text-center">
                  <div className="w-12 h-12 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center mx-auto mb-3">
                    <X className="w-6 h-6 text-red-600 dark:text-red-400" />
                  </div>
                  <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1">{t('common.loadFailed', '加载失败')}</h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{error}</p>
                  <button
                    onClick={handleRetry}
                    className="px-3 py-1.5 bg-blue-500 dark:bg-blue-600 text-white text-xs rounded hover:bg-blue-600 dark:hover:bg-blue-700 transition-colors"
                  >
                    {t('common.retry', '重试')}
                  </button>
                </div>
              </div>
            )}

            {/* Knowledge Bases List */}
            {!isLoading && !error && (
              <div className="space-y-2">
                {filteredKnowledgeBases.length === 0 ? (
                  <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                    <FolderOpen className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    <p>{t('knowledge.selectModal.noMatch', '未找到匹配的知识库')}</p>
                  </div>
                ) : (
                  filteredKnowledgeBases.map(knowledgeBase => {
                    const isSelected = tempSelectedKnowledgeBases.includes(knowledgeBase.id);

                return (
                  <div
                    key={knowledgeBase.id}
                    onClick={() => handleKnowledgeBaseClick(knowledgeBase)}
                    className={`flex items-center justify-between p-3 rounded-md border cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-blue-50 border-blue-200 hover:bg-blue-100 dark:bg-blue-900/30 dark:border-blue-700 dark:hover:bg-blue-900/50'
                        : 'bg-white border-gray-200 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-700 dark:hover:bg-gray-700'
                    }`}
                  >
                    <div className="flex items-start space-x-3">
                      <KBIcon kb={knowledgeBase} selected={isSelected} />
                      <div>
                        <div className={`text-sm font-medium ${isSelected ? 'text-blue-900 dark:text-blue-300' : 'text-gray-800 dark:text-gray-100'}`}>
                          {knowledgeBase.title}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-snug">
                          {knowledgeBase.content}
                        </div>
                        <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                          {t('knowledge.filesCount', { count: knowledgeBase.fileCount ?? 0, defaultValue: `${knowledgeBase.fileCount ?? 0} 文件` })}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => handleKnowledgeBaseClick(knowledgeBase)}
                        className="rounded border-gray-300 dark:border-gray-600 text-blue-600 dark:text-blue-500 focus:ring-blue-500 dark:bg-gray-700"
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

        {/* Knowledge Base Management Link */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
          <button
            className="flex items-center space-x-2 text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 text-sm"
            onClick={() => {
              navigate('/knowledge');
              onClose();
            }}
          >
            <span>{t('knowledge.manage', '管理知识库')}</span>
            <ExternalLink className="w-4 h-4" />
          </button>
        </div>

        {/* Footer - Always Visible */}
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
            {t('knowledge.selectModal.confirmSelection', { count: tempSelectedKnowledgeBases.length, defaultValue: `确认选择 (${tempSelectedKnowledgeBases.length})` })}
          </button>
        </div>
      </div>
    </div>
  );
};

export default KnowledgeBaseSelectionModal;
