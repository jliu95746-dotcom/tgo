import React, { useState, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { UploadCloud, X, CheckCircle, AlertCircle, Loader } from 'lucide-react';

interface FileUploadProgress {
  fileId: string;
  fileName: string;
  progress: number;
  status: 'uploading' | 'processing' | 'completed' | 'error';
  error?: string;
}

interface FileUploadProps {
  onUpload: (files: File[]) => void;
  isVisible: boolean;
  onToggle: () => void;
  uploadProgress?: Map<string, FileUploadProgress>;
  onRemoveUploadProgress?: (fileId: string) => void;
  hasEmbeddingModel?: boolean;
  isCheckingEmbedding?: boolean;
}

interface UploadFile extends File {
  id: string;
  progress: number;
  status: 'uploading' | 'success' | 'error';
  error?: string;
}

/**
 * File Upload Component with Drag & Drop
 * Based on the HTML reference design
 */
export const FileUpload: React.FC<FileUploadProps> = ({
  onUpload,
  isVisible,
  uploadProgress,
  onRemoveUploadProgress,
  hasEmbeddingModel = true,
  isCheckingEmbedding = false
}) => {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [isDragOver, setIsDragOver] = useState(false);

  // Determine if upload should be disabled
  const isUploadDisabled = isCheckingEmbedding || !hasEmbeddingModel;

  // Convert service progress to display format
  const displayUploadFiles = React.useMemo(() => {
    if (!uploadProgress) return [];

    const files = Array.from(uploadProgress.values()).map(progress => ({
      id: progress.fileId,
      name: progress.fileName,
      size: 0, // Size not tracked during upload, will be available after completion
      progress: progress.progress,
      status: progress.status === 'completed' ? 'success' :
        progress.status === 'error' ? 'error' : 'uploading',
      error: progress.error,
    } as UploadFile));

    // Debug logging
    if (files.length > 0) {
      console.log('FileUpload: Displaying progress for files:', files.map(f => ({
        name: f.name,
        progress: f.progress,
        status: f.status
      })));
    }

    return files;
  }, [uploadProgress]);

  // Auto-hide completed uploads after a delay
  React.useEffect(() => {
    const completedFiles = displayUploadFiles.filter(file => file.status === 'success');

    if (completedFiles.length > 0) {
      const timer = setTimeout(() => {
        // This will be handled by the service's cleanup logic
        console.log('Completed uploads should be cleaned up by the service');
      }, 2000);

      return () => clearTimeout(timer);
    }

    return undefined;
  }, [displayUploadFiles]);

  // Handle drag events
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer.files);
    handleFiles(files);
  };

  // Handle file selection
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    handleFiles(files);
    // Reset input
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Process selected files
  const handleFiles = (files: File[]) => {
    // Block upload if embedding model is not configured
    if (isUploadDisabled) {
      return;
    }

    const validFiles = files.filter(file => {
      // Check file size (10MB limit)
      if (file.size > 10 * 1024 * 1024) {
        alert(`${file.name}: ${t('knowledge.upload.maxFileSize')}`);
        return false;
      }

      // Check file type
      const allowedTypes = ['.pdf', '.doc', '.docx', '.txt', '.xlsx', '.xls', '.ppt', '.pptx', '.md', '.markdown', '.html', '.htm'];
      const fileExtension = `.${  file.name.split('.').pop()?.toLowerCase()}`;
      if (!allowedTypes.includes(fileExtension)) {
        alert(`${file.name}: ${t('knowledge.upload.unsupportedFormat')}`);
        return false;
      }

      return true;
    });

    if (validFiles.length > 0) {
      // Call parent upload handler - the service will handle progress tracking
      onUpload(validFiles);
    }
  };

  // Remove upload file from progress (this will be handled by the service)
  const removeUploadFile = (fileId: string) => {
    onRemoveUploadProgress?.(fileId);
  };

  // Get file type icon
  const getFileIcon = (fileName: string | undefined) => {
    if (!fileName) return '📄';
    const extension = fileName.split('.').pop()?.toLowerCase();
    switch (extension) {
      case 'pdf': return '📄';
      case 'doc':
      case 'docx': return '📝';
      case 'txt': return '📃';
      case 'xlsx':
      case 'xls': return '📊';
      case 'ppt':
      case 'pptx': return '📊';
      default: return '📄';
    }
  };

  // Format file size
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))  } ${  sizes[i]}`;
  };

  if (!isVisible && (uploadProgress ? uploadProgress.size === 0 : true)) {
    return null;
  }

  return (
    <div className="mx-6 mt-4 p-6 bg-white/80 dark:bg-gray-800/80 backdrop-blur-md rounded-lg shadow-sm border border-gray-200/60 dark:border-gray-700/60">
      {/* Upload Area */}
      <div
        className={`upload-area rounded-lg p-8 text-center border-2 border-dashed transition-all duration-300 ${isUploadDisabled
          ? 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 cursor-not-allowed opacity-60'
          : isDragOver
            ? 'border-blue-500 dark:border-blue-400 bg-blue-50/50 dark:bg-blue-900/30'
            : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
          }`}
        onDragOver={isUploadDisabled ? undefined : handleDragOver}
        onDragLeave={isUploadDisabled ? undefined : handleDragLeave}
        onDrop={isUploadDisabled ? undefined : handleDrop}
      >
        {isCheckingEmbedding ? (
          <>
            <Loader className="w-12 h-12 mx-auto text-gray-400 dark:text-gray-500 mb-4 animate-spin" />
            <h3 className="text-lg font-medium text-gray-600 dark:text-gray-300 mb-2">
              {t('knowledge.upload.checkingEmbedding', '检查嵌入模型配置...')}
            </h3>
          </>
        ) : (
          <>
            <UploadCloud className={`w-12 h-12 mx-auto mb-4 ${isUploadDisabled ? 'text-gray-300 dark:text-gray-600' : 'text-gray-400 dark:text-gray-500'}`} />
            <h3 className={`text-lg font-medium mb-2 ${isUploadDisabled ? 'text-gray-500 dark:text-gray-500' : 'text-gray-800 dark:text-gray-100'}`}>
              {t('knowledge.upload.title', '点击或拖拽上传文件')}
            </h3>
            <p className={`text-sm mb-4 ${isUploadDisabled ? 'text-gray-400 dark:text-gray-600' : 'text-gray-500 dark:text-gray-400'}`}>
              {t('knowledge.upload.supportedFormats', '支持 PDF, Word, Excel, PowerPoint, Text, Markdown 等格式')}
            </p>

            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.txt,.xlsx,.xls,.ppt,.pptx,.md,.markdown,.html,.htm"
              onChange={handleFileSelect}
              className="hidden"
              disabled={isUploadDisabled}
            />

            <div className="flex flex-col items-center gap-4">
              <button
                onClick={() => !isUploadDisabled && fileInputRef.current?.click()}
                disabled={isUploadDisabled}
                className={`px-4 py-2 rounded-md transition-colors duration-200 ${isUploadDisabled
                  ? 'bg-gray-300 dark:bg-gray-700 text-gray-500 dark:text-gray-400 cursor-not-allowed'
                  : 'bg-blue-500 dark:bg-blue-600 text-white hover:bg-blue-600 dark:hover:bg-blue-700'
                  }`}
              >
                {t('knowledge.upload.selectFiles', '选择文件')}
              </button>
            </div>
          </>
        )}
      </div>

      {/* Upload Progress */}
      {displayUploadFiles.length > 0 && (
        <div className="mt-4">
          <div className="space-y-2">
            {displayUploadFiles.map(file => (
              <div
                key={file.id}
                className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600"
              >
                <div className="flex items-center flex-1 min-w-0">
                  <span className="text-xl mr-3">{getFileIcon(file.name)}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                      {file.name || 'Unknown file'}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {file.size > 0 ? formatFileSize(file.size) : '上传中...'}
                    </p>

                    {/* Progress bar */}
                    {file.status === 'uploading' && (
                      <div className="mt-2">
                        <div className="flex items-center justify-between text-xs text-gray-600 dark:text-gray-300 mb-1">
                          <span>{t('knowledge.upload.uploadProgress', '上传进度')}</span>
                          <span>{Math.round(file.progress)}%</span>
                        </div>
                        <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-1.5">
                          <div
                            className="bg-blue-500 dark:bg-blue-400 h-1.5 rounded-full progress-bar"
                            style={{ width: `${file.progress}%` }}
                          />
                        </div>
                      </div>
                    )}

                    {/* Error message */}
                    {file.status === 'error' && file.error && (
                      <p className="text-xs text-red-500 dark:text-red-400 mt-1">{file.error}</p>
                    )}
                  </div>
                </div>

                <div className="flex items-center ml-4">
                  {/* Status icon */}
                  {file.status === 'uploading' && (
                    <Loader className="w-4 h-4 text-blue-500 dark:text-blue-400 animate-spin mr-2" />
                  )}
                  {file.status === 'success' && (
                    <CheckCircle className="w-4 h-4 text-green-500 dark:text-green-400 mr-2" />
                  )}
                  {file.status === 'error' && (
                    <AlertCircle className="w-4 h-4 text-red-500 dark:text-red-400 mr-2" />
                  )}

                  {/* Remove button */}
                  <button
                    onClick={() => removeUploadFile(file.id)}
                    className="p-1 text-gray-400 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
