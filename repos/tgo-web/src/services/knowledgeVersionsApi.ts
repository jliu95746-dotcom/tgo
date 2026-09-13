import { apiClient } from './api';

export type SourceKind = 'file' | 'qa' | 'website';
export type VersionAction = 'save' | 'submit' | 'publish';
export interface VersionContent {
  question?: string | null;
  answer?: string | null;
  category?: string | null;
  subcategory?: string | null;
  tags?: string[] | null;
  priority?: number | null;
  replacement_file_id?: string | null;
}
export interface KnowledgeVersion {
  id: string;
  number: number;
  state: 'processing' | 'ready' | 'pending_review' | 'published' | 'retired' | 'failed' | 'unchanged';
  author: string;
  created_at: string;
  published_by: string | null;
  published_at: string | null;
  error: string | null;
  restored_from: number | null;
  content: VersionContent;
  preview: string;
}
export interface VersionHistory {
  source_kind: SourceKind;
  source_id: string;
  active_number: number | null;
  disabled: boolean;
  retention: number;
  versions: KnowledgeVersion[];
}
export interface CleanupResult { removable_numbers: number[]; removed: number; file_cleanup_pending: number }
const base = '/v1/rag/knowledge-versions';
export const knowledgeVersionsApi = {
  history: (kind: SourceKind, id: string) => apiClient.get<VersionHistory>(`${base}/${kind}/${id}`),
  change: (kind: SourceKind, id: string, content: VersionContent, action: VersionAction) =>
    apiClient.post<KnowledgeVersion>(`${base}/${kind}/${id}`, { content, action }),
  transition: (id: string, action: 'publish' | 'restore' | 'submit' | 'discard') =>
    apiClient.post<KnowledgeVersion>(`${base}/versions/${id}/${action}`),
  disable: (kind: SourceKind, id: string) => apiClient.post<VersionHistory>(`${base}/${kind}/${id}/disable`),
  enable: (kind: SourceKind, id: string) => apiClient.post<VersionHistory>(`${base}/${kind}/${id}/enable`),
  cleanup: (kind: SourceKind, id: string, retention: number, confirm: boolean, expectedNumbers?: number[]) =>
    apiClient.post<CleanupResult>(`${base}/${kind}/${id}/cleanup`, { retention, confirm, expected_numbers: expectedNumbers }),
  upload: (id: string, file: File) => {
    const data = new FormData();
    data.append('file', file);
    return apiClient.postFormData<{ id: string }>(`${base}/file/${id}/upload`, data);
  },
};
