/**
 * File Management Service for Knowledge Base Detail Page
 * Handles file operations using the RAG Files API
 */

import { KnowledgeBaseApiService, isAuthenticated } from './knowledgeBaseApi';
import { transformFilesToKnowledgeFiles } from '@/utils/knowledgeBaseTransforms';
import { uploadFileWithProgress, type UploadProgressEvent } from './fileUploadService';
import type { KnowledgeFile } from '@/types';
import { useOnboardingStore } from '@/stores/onboardingStore';

export interface FileUploadProgress {
  fileId: string;
  fileName: string;
  progress: number;
  status: 'uploading' | 'processing' | 'completed' | 'error';
  error?: string;
}

export interface FileManagementState {
  files: KnowledgeFile[];
  isLoading: boolean;
  uploadProgress: Map<string, FileUploadProgress>;
  error: string | null;
  hasError: boolean;
}

/**
 * File Management Service Class
 */
export class FileManagementService {
  private collectionId: string;
  private listeners: Set<(state: FileManagementState) => void> = new Set();
  private state: FileManagementState = {
    files: [],
    isLoading: false,
    uploadProgress: new Map(),
    error: null,
    hasError: false,
  };

  constructor(collectionId: string) {
    this.collectionId = collectionId;
  }

  // Subscribe to state changes
  subscribe(listener: (state: FileManagementState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // Notify all listeners of state changes
  private notify(): void {
    this.listeners.forEach(listener => listener(this.state));
  }

  // Update state
  private setState(updates: Partial<FileManagementState>): void {
    this.state = { ...this.state, ...updates };
    this.notify();
  }

  // Get current state
  getState(): FileManagementState {
    return this.state;
  }

  // Load files for the collection
  async loadFiles(): Promise<void> {
    this.setState({
      isLoading: true,
      error: null,
      hasError: false
    });

    // Check authentication before loading files
    if (!isAuthenticated()) {
      const errorMessage = '身份验证失败，请重新登录后再试';
      this.setState({
        isLoading: false,
        error: errorMessage,
        hasError: true
      });
      throw new Error(errorMessage);
    }

    try {
      const response = await KnowledgeBaseApiService.getFiles({
        collection_id: this.collectionId,
        limit: 100, // Load all files for now
      });

      const files = transformFilesToKnowledgeFiles(response.data || []);
      this.setState({
        files,
        isLoading: false,
        error: null,
        hasError: false
      });
    } catch (error) {
      console.error('Failed to load files:', error);
      const errorMessage = error instanceof Error ? error.message : '加载文件列表失败';
      this.setState({
        isLoading: false,
        error: errorMessage,
        hasError: true
      });
      throw error;
    }
  }

  // Upload single file with progress tracking
  async uploadFile(
    file: File,
    metadata?: {
      description?: string;
      tags?: string[];
      language?: string;
      is_qa_mode?: boolean;
    }
  ): Promise<void> {
    // Check authentication before starting upload
    if (!isAuthenticated()) {
      const error = new Error('身份验证失败，请重新登录后再试');
      throw error;
    }

    const fileId = `upload_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`;

    // Initialize upload progress
    const initialProgress = new Map(this.state.uploadProgress);
    initialProgress.set(fileId, {
      fileId,
      fileName: file.name,
      progress: 0,
      status: 'uploading',
    });
    this.setState({ uploadProgress: initialProgress });

    try {
      // Use the new upload service with real progress tracking
      const uploadedFile = await uploadFileWithProgress(file, {
        collection_id: this.collectionId,
        ...metadata,
        onProgress: (event: UploadProgressEvent) => {
          // Update progress state with real progress data
          const uploadProgress = new Map(this.state.uploadProgress);
          uploadProgress.set(fileId, {
            fileId, // Use our local fileId for consistency
            fileName: event.fileName,
            progress: event.progress,
            status: event.status,
            error: event.error,
          });
          this.setState({ uploadProgress });
        },
      });

      // Add file to the list
      const newFile = transformFilesToKnowledgeFiles([uploadedFile])[0];
      this.setState({
        files: [newFile, ...this.state.files],
      });

      // Mark onboarding task as completed
      try {
        useOnboardingStore.getState().markTaskCompleted('knowledgeBaseUploaded');
      } catch (error) {
        console.error('Failed to mark onboarding task completed:', error);
      }

      // Remove from upload progress immediately after success
      const updatedProgress = new Map(this.state.uploadProgress);
      updatedProgress.delete(fileId);
      this.setState({ uploadProgress: updatedProgress });

    } catch (error) {
      console.error('File upload failed:', error);

      // Update progress to error
      const uploadProgress = new Map(this.state.uploadProgress);
      uploadProgress.set(fileId, {
        fileId,
        fileName: file.name,
        progress: 0,
        status: 'error',
        error: error instanceof Error ? error.message : 'Upload failed',
      });
      this.setState({ uploadProgress: new Map(uploadProgress) });

      throw error;
    }
  }

  // Upload multiple files
  async uploadFiles(
    files: File[],
    metadata?: {
      description?: string;
      tags?: string[];
      language?: string;
    }
  ): Promise<{ successCount: number; failedCount: number; errors: Error[] }> {
    const uploadPromises = files.map(file => this.uploadFile(file, metadata));

    const results = await Promise.allSettled(uploadPromises);

    const errors: Error[] = [];
    let successCount = 0;
    let failedCount = 0;

    results.forEach((result) => {
      if (result.status === 'fulfilled') {
        successCount++;
      } else {
        failedCount++;
        errors.push(result.reason instanceof Error ? result.reason : new Error(String(result.reason)));
      }
    });

    // If all files failed, throw the first error
    if (failedCount > 0 && successCount === 0) {
      throw errors[0];
    }

    return { successCount, failedCount, errors };
  }

  /**
   * Clear a specific upload progress entry (e.g., user closes the item in UI)
   */
  clearUploadProgress(fileId: string): void {
    const uploadProgress = new Map(this.state.uploadProgress);
    if (uploadProgress.has(fileId)) {
      uploadProgress.delete(fileId);
      this.setState({ uploadProgress });
    }
  }

  // Delete file
  async deleteFile(fileId: string): Promise<void> {
    // Check authentication before deleting
    if (!isAuthenticated()) {
      const error = new Error('身份验证失败，请重新登录后再试');
      throw error;
    }

    try {
      await KnowledgeBaseApiService.deleteFile(fileId);

      // Remove file from the list
      this.setState({
        files: this.state.files.filter(file => file.id !== fileId),
      });
    } catch (error) {
      console.error('Failed to delete file:', error);
      throw error;
    }
  }

  // Download file
  async downloadFile(fileId: string): Promise<void> {
    // Check authentication before downloading
    if (!isAuthenticated()) {
      const error = new Error('身份验证失败，请重新登录后再试');
      throw error;
    }

    try {
      const response = await KnowledgeBaseApiService.downloadFile(fileId);

      // Create download link
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;

      // Get filename from response headers or use file ID
      const contentDisposition = response.headers.get('content-disposition');
      let filename = `file_${fileId}`;
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="(.+)"/);
        if (filenameMatch) {
          filename = filenameMatch[1];
        }
      }

      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Failed to download file:', error);
      throw error;
    }
  }

  // Get file statistics
  getStatistics(): {
    totalFiles: number;
    totalSize: number;
    statusCounts: Record<string, number>;
  } {
    const { files } = this.state;

    const totalFiles = files.length;
    const totalSize = files.reduce((sum, file) => sum + file.sizeBytes, 0);
    const statusCounts: Record<string, number> = {};

    files.forEach(file => {
      statusCounts[file.statusType] = (statusCounts[file.statusType] || 0) + 1;
    });

    return {
      totalFiles,
      totalSize,
      statusCounts,
    };
  }

  // Filter files
  filterFiles(searchTerm: string, fileTypeFilter: string): KnowledgeFile[] {
    let filtered = this.state.files;

    // Search filter
    if (searchTerm.trim()) {
      filtered = filtered.filter(file =>
        file.name.toLowerCase().includes(searchTerm.toLowerCase())
      );
    }

    // Type filter
    if (fileTypeFilter) {
      filtered = filtered.filter(file => file.type === fileTypeFilter);
    }

    return filtered;
  }

  // Sort files
  sortFiles(files: KnowledgeFile[], sortField: 'name' | 'size' | 'date', sortDirection: 'asc' | 'desc'): KnowledgeFile[] {
    return [...files].sort((a, b) => {
      let aValue: any, bValue: any;

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
          aValue = new Date(a.uploadDate);
          bValue = new Date(b.uploadDate);
          break;
        default:
          return 0;
      }

      if (aValue < bValue) return sortDirection === 'asc' ? -1 : 1;
      if (aValue > bValue) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
  }
}

export default FileManagementService;
