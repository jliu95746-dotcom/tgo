import React, { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowUpDown,
  Download,
  Trash2,
  FileX,
  Loader,
  MessageSquare
} from 'lucide-react';
import type { KnowledgeFile } from '@/types';

interface DocumentListProps {
  documents: KnowledgeFile[];
  onEditFile?: (fileId: string) => void;
  isLoading: boolean;
  onDownload: (docId: string) => void;
  onDelete: (docId: string) => void;
  onViewQA: (docId: string, docName: string) => void;
  // Search and filter from parent
  searchTerm: string;
  fileTypeFilter: string;
  // Loading states for individual operations
  downloadingFiles?: Set<string>;
  deletingFiles?: Set<string>;
}

type SortField = 'name' | 'size' | 'date';
type SortDirection = 'asc' | 'desc';

/**
 * Document List Component
 * Based on the HTML reference design with sorting and filtering
 */
export const DocumentList: React.FC<DocumentListProps> = ({
  documents,
  onEditFile,
  isLoading,
  onDownload,
  onDelete,
  onViewQA,
  searchTerm,
  fileTypeFilter,
  downloadingFiles = new Set(),
  deletingFiles = new Set()
}) => {
  const { t } = useTranslation();

  const [sortField, setSortField] = useState<SortField>('date');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  // Get file type icon
  const getFileIcon = (type: string) => {
    switch (type) {
      case 'pdf': return '📄';
      case 'doc': return '📝';
      case 'txt': return '📃';
      case 'xlsx': return '📊';
      case 'ppt': return '📊';
      default: return '📄';
    }
  };

  // Get status badge
  const getStatusBadge = (statusType: string, status: string) => {
    const baseClasses = "px-2 py-1 text-xs font-medium rounded-full";

    switch (statusType) {
      case 'success':
        return (
          <span className={`${baseClasses} bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300`}>
            {status}
          </span>
        );
      case 'processing':
        return (
          <span className={`${baseClasses} bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300`}>
            {status}
          </span>
        );
      case 'error':
        return (
          <span className={`${baseClasses} bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300`}>
            {status}
          </span>
        );
      default:
        return (
          <span className={`${baseClasses} bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200`}>
            {status}
          </span>
        );
    }
  };

  // Filter and sort documents
  const filteredAndSortedDocuments = useMemo(() => {
    const filtered = documents.filter(doc => {
      const matchesSearch = doc.name.toLowerCase().includes(searchTerm.toLowerCase());
      const matchesType = !fileTypeFilter || doc.type === fileTypeFilter;
      return matchesSearch && matchesType;
    });

    // Sort documents
    filtered.sort((a, b) => {
      let aValue: string | number, bValue: string | number;

      switch (sortField) {
        case 'name':
          aValue = a.name.toLowerCase();
          bValue = b.name.toLowerCase();
          break;
        case 'size':
          aValue = a.sizeBytes;
          bValue = b.sizeBytes;
          break;
        case 'date':
          aValue = new Date(a.created_at || a.uploadDate).getTime();
          bValue = new Date(b.created_at || b.uploadDate).getTime();
          break;
        default:
          return 0;
      }

      if (aValue < bValue) return sortDirection === 'asc' ? -1 : 1;
      if (aValue > bValue) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });

    return filtered;
  }, [documents, searchTerm, fileTypeFilter, sortField, sortDirection]);

  // Handle sort
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };



  if (isLoading) {
    return (
      <div className="flex-grow overflow-y-auto p-6">
        <div className="bg-white/80 dark:bg-gray-800/80 backdrop-blur-md rounded-lg shadow-sm border border-gray-200/60 dark:border-gray-700/60 p-8">
          <div className="text-center py-8">
            <Loader className="w-8 h-8 mx-auto text-gray-500 dark:text-gray-400 animate-spin mb-4" />
            <p className="text-gray-500 dark:text-gray-400">{t('knowledge.detail.loading')}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">

      {/* Document Table */}
      <div className="bg-white/80 dark:bg-gray-800/80 backdrop-blur-md rounded-lg shadow-sm border border-gray-200/60 dark:border-gray-700/60 overflow-hidden">
        {/* Table Header */}
        <div className="hidden xl:grid grid-cols-[minmax(0,2fr)_80px_120px_minmax(0,2fr)_100px] gap-4 px-4 py-3 border-b border-gray-200/60 dark:border-gray-700/60 bg-gray-50/50 dark:bg-gray-900/50">
          <div
            className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider flex items-center cursor-pointer hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            onClick={() => handleSort('name')}
          >
            {t('knowledge.detail.documentName')}
            <ArrowUpDown className="w-3 h-3 ml-1" />
          </div>
          <div
            className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider flex items-center cursor-pointer hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            onClick={() => handleSort('size')}
          >
            {t('knowledge.detail.fileSize')}
            <ArrowUpDown className="w-3 h-3 ml-1" />
          </div>
          <div
            className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider flex items-center cursor-pointer hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            onClick={() => handleSort('date')}
          >
            {t('knowledge.detail.uploadTime')}
            <ArrowUpDown className="w-3 h-3 ml-1" />
          </div>
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
            {t('knowledgeOnboarding.parse')}
          </div>
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider text-right pr-2">
            {t('knowledge.detail.actions')}
          </div>
        </div>

        {/* Table Body */}
        <div>
          {filteredAndSortedDocuments.length === 0 ? (
            <div className="text-center py-12">
              <FileX className="w-16 h-16 mx-auto text-gray-300 dark:text-gray-600 mb-4" />
              <h3 className="text-lg font-medium text-gray-800 dark:text-gray-100 mb-2">
                {t('knowledge.detail.noDocuments')}
              </h3>
              <p className="text-gray-500 dark:text-gray-400 mb-4">
                {t('knowledge.detail.noDocumentsDesc')}
              </p>
            </div>
          ) : (
            filteredAndSortedDocuments.map((doc, index) => (
              <div
                key={doc.id}
                className={`grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_80px_120px_minmax(0,2fr)_100px] gap-3 px-4 py-3 items-center hover:bg-gray-50/30 dark:hover:bg-gray-700/30 transition-colors ${index < filteredAndSortedDocuments.length - 1 ? 'border-b border-gray-200/60 dark:border-gray-700/60' : ''
                  }`}
              >
                {/* Document Name */}
                <div className="flex items-center space-x-3 min-w-0">
                  <span className="text-xl flex-shrink-0">{getFileIcon(doc.type)}</span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{doc.name}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">{doc.type.toUpperCase()}</p>
                  </div>
                </div>

                {/* File Size */}
                <div className="text-sm text-gray-900 dark:text-gray-100 font-medium">
                  {doc.size}
                </div>

                {/* Upload Date */}
                <div className="text-sm text-gray-900 dark:text-gray-100">
                  {doc.uploadDate}
                </div>

                {/* Status */}
                <div>
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="text-xs text-gray-500">{t('knowledgeOnboarding.parse')}：</span>
                    {getStatusBadge(doc.statusType, doc.status)}
                    <span className="text-xs text-gray-500">{t('knowledgeOnboarding.chunks', { count: doc.document_count || 0 })}</span>
                  </div>
                  {onEditFile && <button type="button" disabled={doc.statusType === 'processing'} onClick={() => onEditFile(doc.id)} className="rounded border border-blue-200 px-2 py-1 text-xs text-blue-600 disabled:opacity-50 dark:text-blue-300">{t('knowledgeOnboarding.editMaterial')}</button>}
                  {doc.statusType === 'error' && doc.error_message && (
                    <p className="mt-1 text-xs text-red-600 dark:text-red-400 break-words">
                      {doc.error_message}
                    </p>
                  )}
                </div>

                {/* Actions */}
                <div className="flex items-center space-x-1">
                  <button
                    onClick={() => onViewQA(doc.id, doc.name)}
                    className="p-1.5 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-md transition-colors"
                    title={t('knowledge.detail.viewQA', '查看问答对')}
                  >
                    <MessageSquare className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onDownload(doc.id)}
                    disabled={downloadingFiles.has(doc.id)}
                    className="p-1.5 text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/30 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    title={downloadingFiles.has(doc.id) ? '下载中...' : '下载文件'}
                  >
                    {downloadingFiles.has(doc.id) ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Download className="w-4 h-4" />
                    )}
                  </button>
                  <button
                    onClick={() => onDelete(doc.id)}
                    disabled={deletingFiles.has(doc.id)}
                    className="p-1.5 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    title={deletingFiles.has(doc.id) ? '删除中...' : '删除文件'}
                  >
                    {deletingFiles.has(doc.id) ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Trash2 className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
