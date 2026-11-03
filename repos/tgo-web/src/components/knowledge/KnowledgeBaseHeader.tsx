import React from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, RefreshCw, Upload, Search } from 'lucide-react';
import { getTagClasses } from '@/utils/tagColors';

interface KnowledgeBaseHeaderProps {
  knowledgeBase: {
    id: string;
    name: string;
    description?: string;
    category?: string;
    tags?: (string | { name: string })[];
  };
  onBack: () => void;
  onRefresh: () => void;
  onToggleUpload: () => void;
  onPasteText?: () => void;
  isUploadVisible: boolean;
  // Search and filter props
  searchTerm: string;
  onSearchChange: (term: string) => void;
  fileTypeFilter: string;
  onFilterChange: (filter: string) => void;
  // Stats props
  totalDocuments: number;
  totalSize: string;
  lastUpdated: string;
}

/**
 * Knowledge Base Header Component
 * Based on the HTML reference design
 */
export const KnowledgeBaseHeader: React.FC<KnowledgeBaseHeaderProps> = ({
  knowledgeBase,
  onBack,
  onRefresh,
  onToggleUpload,
  onPasteText,
  isUploadVisible,
  searchTerm,
  onSearchChange,
  fileTypeFilter,
  onFilterChange,
  totalDocuments,
  totalSize,
  lastUpdated
}) => {
  const { t } = useTranslation();



  return (
    <header className="px-6 py-4 border-b border-gray-200/80 dark:border-gray-700/80 sticky top-0 bg-white/60 dark:bg-gray-800/60 backdrop-blur-lg z-10">
      {/* Main Header Row - matches HTML reference structure */}
      <div className="flex flex-wrap justify-between items-start gap-3">
        <div className="flex min-w-0 w-full sm:flex-1 items-start gap-3">
          {/* Back Button */}
          <button
            onClick={onBack}
            className="p-2 text-gray-500 dark:text-gray-400 hover:bg-gray-200/70 dark:hover:bg-gray-700/70 rounded-md transition-colors duration-200"
            title={t('knowledge.detail.backToList')}
          >
            <ArrowLeft className="w-5 h-5" />
          </button>

          {/* Title and Description */}
          <div className="min-w-0">
            <h2 className="break-words text-lg font-semibold text-gray-800 dark:text-gray-100">
              {knowledgeBase.name}
            </h2>
            {knowledgeBase.description && (
              <details className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                <summary className="cursor-pointer">{t('knowledgeOnboarding.readBrief')}</summary>
                <p className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words">{knowledgeBase.description}</p>
                {knowledgeBase.description.length > 500 && <p className="mt-2 text-amber-600 dark:text-amber-400">{t('knowledgeOnboarding.legacyBrief')}</p>}
              </details>
            )}

            {/* Tags Display */}
            {knowledgeBase.tags && knowledgeBase.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {knowledgeBase.tags.map((tag, tagIndex: number) => {
                  const tagName = typeof tag === 'string' ? tag : tag.name || String(tag);
                  return (
                    <span
                      key={tagIndex}
                      className={getTagClasses(tagName, {
                        size: 'sm',
                        includeHover: true,
                        includeBorder: true,
                        rounded: true
                      })}
                    >
                      {tagName}
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
          {onPasteText && <button onClick={onPasteText} className="rounded-md border border-blue-300 px-3 py-1.5 text-sm text-blue-600 dark:text-blue-300">{t('knowledgeOnboarding.paste')}</button>}
          <button
            onClick={onRefresh}
            className="p-1.5 text-gray-500 dark:text-gray-400 hover:bg-gray-200/70 dark:hover:bg-gray-700/70 rounded-md transition-colors duration-200"
            title={t('knowledge.detail.refresh')}
          >
            <RefreshCw className="w-4 h-4" />
          </button>

          <button
            onClick={onToggleUpload}
            className={`flex items-center px-3 py-1.5 text-sm rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 transition-colors duration-200 ${
              isUploadVisible
                ? 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600'
                : 'bg-blue-500 dark:bg-blue-600 text-white hover:bg-blue-600 dark:hover:bg-blue-700'
            }`}
          >
            <Upload className="w-4 h-4 mr-1" />
            <span>{t('knowledgeOnboarding.replaceUpload')}</span>
          </button>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="mt-4 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div className="relative flex-grow max-w-xs">
          <input
            type="text"
            placeholder={t('knowledge.detail.searchPlaceholder')}
            value={searchTerm}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 text-sm border border-gray-300/70 dark:border-gray-600 rounded-md focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white/80 dark:bg-gray-700/80 dark:text-gray-100 dark:placeholder-gray-400"
          />
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 transform -translate-y-1/2 text-gray-400 dark:text-gray-500" />
        </div>

        <div className="flex items-center space-x-2 flex-shrink-0">
          <span className="text-sm text-gray-500 dark:text-gray-400">{t('knowledge.detail.filter')}:</span>
          <select
            value={fileTypeFilter}
            onChange={(e) => onFilterChange(e.target.value)}
            className="text-sm border border-gray-300/70 dark:border-gray-600 rounded-md py-1.5 pl-2 pr-7 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white/80 dark:bg-gray-700/80 dark:text-gray-100 appearance-none"
          >
            <option value="">{t('knowledge.detail.allTypes')}</option>
            <option value="pdf">PDF</option>
            <option value="doc">Word</option>
            <option value="txt">文本</option>
            <option value="xlsx">Excel</option>
            <option value="ppt">PowerPoint</option>
          </select>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-gray-600 dark:text-gray-300">
        <span>{t('knowledge.detail.totalDocuments', { count: totalDocuments })}</span>
        <span>{t('knowledge.detail.totalSize', { size: totalSize })}</span>
        <span>{t('knowledge.detail.lastUpdated', { date: lastUpdated })}</span>
      </div>
    </header>
  );
};
