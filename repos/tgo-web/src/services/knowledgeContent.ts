import { apiClient } from './api';
import { KnowledgeBaseApiService } from './knowledgeBaseApi';
import i18n from '@/i18n';

interface Chunk { content: string; chunk_index: number }
interface ChunkPage { data: Chunk[]; pagination: { has_next: boolean } }
export interface EditableKnowledge { body: string; parsed: boolean }

export async function loadKnowledgeBody(fileId: string): Promise<EditableKnowledge> {
  const file = await KnowledgeBaseApiService.getFile(fileId);
  if (file.file_size > 10 * 1024 * 1024) throw new Error(i18n.t('knowledgeOnboarding.editTooLarge'));
  if (file.content_type.startsWith('text/') || /\.(md|txt|csv|html?)$/i.test(file.original_filename)) {
    const response = await KnowledgeBaseApiService.downloadFile(fileId);
    return { body: await response.text(), parsed: false };
  }
  const chunks: Chunk[] = [];
  let more = true;
  while (more) {
    const response = await apiClient.get<ChunkPage>(`/v1/rag/files/${encodeURIComponent(fileId)}/documents?limit=100&offset=${chunks.length}`);
    chunks.push(...response.data);
    more = response.pagination.has_next && response.data.length > 0;
  }
  if (!chunks.length) throw new Error(i18n.t('knowledgeOnboarding.noEditableBody'));
  chunks.sort((a, b) => a.chunk_index - b.chunk_index);
  let body = '';
  for (const chunk of chunks) {
    let overlap = Math.min(body.length, chunk.content.length, 2000);
    while (overlap >= 30 && body.slice(-overlap) !== chunk.content.slice(0, overlap)) overlap--;
    body += overlap >= 30 ? chunk.content.slice(overlap) : `${body ? '\n\n' : ''}${chunk.content}`;
  }
  return { body, parsed: true };
}
