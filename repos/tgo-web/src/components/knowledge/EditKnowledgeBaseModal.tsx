import React, { useState, useEffect } from 'react';
import { X, FileText, Globe, MessageSquare, Loader2, FolderOpen } from 'lucide-react';
import { TagInput } from '@/components/ui/TagInput';
import type { KnowledgeBaseItem, KnowledgeBaseType } from '@/types';
import { useTranslation } from 'react-i18next';

// Helper function to get default icon based on knowledge base type
const getDefaultIconByType = (type: KnowledgeBaseType): string => {
  switch (type) {
    case 'file': return 'FileText';
    case 'website': return 'Globe';
    case 'qa': return 'MessageSquare';
    default: return 'FileText';
  }
};

interface EditKnowledgeBaseModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (id: string, data: { name: string; description: string; icon: string; tags: string[] }) => Promise<void>;
  knowledgeBase: KnowledgeBaseItem | null;
  isLoading?: boolean;
}

export const EditKnowledgeBaseModal: React.FC<EditKnowledgeBaseModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  knowledgeBase,
  isLoading = false
}) => {
  const { t } = useTranslation();

  const [formData, setFormData] = useState({
    name: '',
    description: '',
    icon: 'FileText',
    tags: [] as string[]
  });
  const [errors, setErrors] = useState({
    name: '',
    description: ''
  });

  // Initialize form data when modal opens or knowledge base changes
  useEffect(() => {
    if (isOpen && knowledgeBase) {
      // Extract tags from the knowledge base
      const existingTags = Array.isArray(knowledgeBase.tags)
        ? knowledgeBase.tags.map(tag => typeof tag === 'string' ? tag : tag.name).filter(Boolean)
        : [];

      setFormData({
        name: knowledgeBase.title || '',
        description: knowledgeBase.content || '',
        icon: knowledgeBase.icon || getDefaultIconByType(knowledgeBase.type || 'file'),
        tags: existingTags
      });
      setErrors({ name: '', description: '' });
    }
  }, [isOpen, knowledgeBase]);

  const validateForm = (): boolean => {
    const newErrors = { name: '', description: '' };
    let isValid = true;

    // Validate name
    if (!formData.name.trim()) {
      newErrors.name = t('knowledge.validation.nameRequired', '知识库名称不能为空');
      isValid = false;
    } else if (formData.name.trim().length < 2) {
      newErrors.name = t('knowledge.validation.nameMin', '知识库名称至少需要2个字符');
      isValid = false;
    } else if (formData.name.trim().length > 50) {
      newErrors.name = t('knowledge.validation.nameMax', '知识库名称不能超过50个字符');
      isValid = false;
    }

    setErrors(newErrors);
    return isValid;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!knowledgeBase || !validateForm()) {
      return;
    }

    try {
      await onSubmit(knowledgeBase.id, {
        name: formData.name.trim(),
        description: formData.description.trim(),
        icon: formData.icon,
        tags: formData.tags
      });
      onClose();
    } catch (error) {
      // Error handling is done by the parent component
      console.error('Failed to update knowledge base:', error);
    }
  };

  const handleInputChange = (field: 'name' | 'description', value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }));

    // Clear error when user starts typing
    if (errors[field]) {
      setErrors(prev => ({ ...prev, [field]: '' }));
    }
  };

  const handleTagsChange = (tags: string[]) => {
    setFormData(prev => ({ ...prev, tags }));
  };

  // Get the icon component based on type
  const getTypeIcon = () => {
    switch (knowledgeBase?.type) {
      case 'website':
        return <Globe className="w-5 h-5 text-green-600 dark:text-green-400" />;
      case 'qa':
        return <MessageSquare className="w-5 h-5 text-purple-600 dark:text-purple-400" />;
      case 'file':
      default:
        return <FileText className="w-5 h-5 text-blue-600 dark:text-blue-400" />;
    }
  };

  const getTypeLabel = () => {
    switch (knowledgeBase?.type) {
      case 'website':
        return t('knowledge.typeWebsite', '网站');
      case 'qa':
        return t('knowledge.typeQA', '问答');
      case 'file':
      default:
        return t('knowledge.typeFile', '文件');
    }
  };

  const handleClose = () => {
    if (!isLoading) {
      onClose();
    }
  };

  if (!isOpen || !knowledgeBase) return null;

  return (
    <div className="fixed inset-0 bg-black/50 dark:bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header with gradient background */}
        <div className="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-gray-800 dark:to-gray-800 p-6 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-white dark:bg-gray-700 rounded-xl shadow-sm">
                <FolderOpen className="w-6 h-6 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100">
                  {t('knowledge.edit', '编辑知识库')}
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                  {t('knowledge.editDesc', '修改知识库的基本信息')}
                </p>
              </div>
            </div>
            <button
              onClick={handleClose}
              disabled={isLoading}
              className="p-2 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-all duration-200 disabled:opacity-50"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
          <div className="p-6 overflow-y-auto flex-1 space-y-5">
            {/* Type Display (Read-only) */}
            <div className="flex items-center gap-3 p-4 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-gray-100 dark:border-gray-700">
              <div className="p-2 bg-white dark:bg-gray-700 rounded-lg shadow-sm">
                {getTypeIcon()}
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-800 dark:text-gray-100">
                  {getTypeLabel()}
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {t('knowledge.typeReadonly', '知识库类型创建后不可更改')}
                </p>
              </div>
            </div>

            {/* Website Crawl Info (Read-only, only for website type) */}
            {knowledgeBase.type === 'website' && knowledgeBase.crawlConfig && (
              <div className="p-4 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl">
                <div className="flex items-center gap-2 mb-3">
                  <Globe className="w-4 h-4 text-green-600 dark:text-green-400" />
                  <span className="text-sm font-medium text-green-700 dark:text-green-300">
                    {t('knowledge.crawl.info', '爬取配置')}
                  </span>
                </div>
                <div className="space-y-2 text-sm text-gray-600 dark:text-gray-400">
                  <p className="flex items-center gap-2">
                    <span className="font-medium text-gray-700 dark:text-gray-300">{t('knowledge.crawl.startUrl', '起始URL')}:</span>
                    <span className="truncate">{knowledgeBase.crawlConfig.start_url}</span>
                  </p>
                  <div className="flex gap-6">
                    <p><span className="font-medium text-gray-700 dark:text-gray-300">{t('knowledge.crawl.maxPages', '最大页面数')}:</span> {knowledgeBase.crawlConfig.max_pages || 100}</p>
                    <p><span className="font-medium text-gray-700 dark:text-gray-300">{t('knowledge.crawl.maxDepth', '最大深度')}:</span> {knowledgeBase.crawlConfig.max_depth || 3}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Basic Info Section */}
            <div className="bg-gray-50 dark:bg-gray-800/50 rounded-xl p-5 space-y-4 border border-gray-100 dark:border-gray-700">
              <div className="flex items-center gap-3 pb-3 border-b border-gray-200 dark:border-gray-700">
                <FileText className="w-5 h-5 text-gray-500 dark:text-gray-400" />
                <h3 className="font-medium text-gray-800 dark:text-gray-100">{t('knowledge.basicInfo', '基本信息')}</h3>
              </div>

              {/* Name Field */}
              <div>
                <label htmlFor="edit-kb-name" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">
                  {t('knowledge.name', '知识库名称')} <span className="text-red-500 dark:text-red-400">*</span>
                </label>
                <input
                  id="edit-kb-name"
                  type="text"
                  value={formData.name}
                  onChange={(e) => handleInputChange('name', e.target.value)}
                  placeholder={t('knowledge.namePlaceholder', '请输入知识库名称')}
                  disabled={isLoading}
                  className={`w-full px-4 py-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200 bg-white dark:bg-gray-700 dark:text-gray-100 ${
                    errors.name ? 'border-red-300 bg-red-50 dark:border-red-600 dark:bg-red-950' : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
                  } ${isLoading ? 'bg-gray-100 dark:bg-gray-800 cursor-not-allowed' : ''}`}
                  maxLength={50}
                />
                {errors.name && (
                  <p className="mt-1.5 text-sm text-red-600 dark:text-red-400">{errors.name}</p>
                )}
                <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                  {formData.name.length}/50 {t('common.characters', '字符')}
                </p>
              </div>

              {/* Description Field */}
              <div>
                <label htmlFor="edit-kb-description" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">
                  {t('knowledgeOnboarding.brief')}
                </label>
                <textarea
                  id="edit-kb-description"
                  value={formData.description}
                  onChange={(e) => handleInputChange('description', e.target.value)}
                  placeholder={t('knowledgeOnboarding.briefPlaceholder')}
                  disabled={isLoading}
                  rows={3}
                  className={`w-full px-4 py-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all duration-200 resize-vertical bg-white dark:bg-gray-700 dark:text-gray-100 ${
                    errors.description ? 'border-red-300 bg-red-50 dark:border-red-600 dark:bg-red-950' : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
                  } ${isLoading ? 'bg-gray-100 dark:bg-gray-800 cursor-not-allowed' : ''}`}
                />
                {errors.description && (
                  <p className="mt-1.5 text-sm text-red-600 dark:text-red-400">{errors.description}</p>
                )}
                <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                  {t('knowledgeOnboarding.briefHint')}
                </p>
              </div>

              {/* Tags Field */}
              <div>
                <label htmlFor="edit-kb-tags" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">
                  {t('knowledge.tags', '知识库标签')} <span className="text-gray-400 dark:text-gray-500 font-normal">({t('common.optional', '可选')})</span>
                </label>
                <TagInput
                  tags={formData.tags}
                  onTagsChange={handleTagsChange}
                  placeholder={t('knowledge.tagsPlaceholder', '输入标签并按回车键添加')}
                  maxTags={10}
                  maxTagLength={20}
                  disabled={isLoading}
                />
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="flex justify-end space-x-3 p-6 border-t border-gray-200 dark:border-gray-700 flex-shrink-0 bg-gray-50 dark:bg-gray-800/50">
            <button
              type="button"
              onClick={handleClose}
              disabled={isLoading}
              className="px-5 py-2.5 text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-gray-500 focus:ring-offset-2 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('common.cancel', '取消')}
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-5 py-2.5 text-sm font-medium text-white bg-blue-600 dark:bg-blue-600 border border-transparent rounded-lg hover:bg-blue-700 dark:hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center shadow-sm"
            >
              {isLoading ? (
                <>
                  <Loader2 className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" />
                  {t('common.updating', '更新中...')}
                </>
              ) : (
                t('knowledge.update', '更新知识库')
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
