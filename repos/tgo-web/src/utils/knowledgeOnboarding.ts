import type { KnowledgeFile, KnowledgeGovernanceRecord } from '@/types';

export type KnowledgeUseState = 'unknown' | 'archived' | 'parsing' | 'failed' | 'empty' | 'unconfigured' | 'draft' | 'pending_review' | 'rejected' | 'revoked' | 'disabled' | 'untrusted' | 'scheduled' | 'expired' | 'no_channel' | 'authorized';

/** Parsing and permission are independent; never infer AI availability from upload success. */
export function getKnowledgeUseState(file: KnowledgeFile, record: KnowledgeGovernanceRecord | undefined, loaded: boolean, now = Date.now()): KnowledgeUseState {
  if (file.processing_status === 'archived') return 'archived';
  if (file.processing_status === 'failed' || file.statusType === 'error') return 'failed';
  if (file.processing_status !== 'completed') return 'parsing';
  if (!file.document_count || file.document_count < 0) return 'empty';
  if (!loaded) return 'unknown';
  if (!record) return 'unconfigured';
  if (record.review_status !== 'approved') return record.review_status;
  if (!record.allow_automatic_reply) return 'disabled';
  if (record.source_origin === 'customer') return 'untrusted';
  const effective = Date.parse(record.effective_at);
  const expires = record.expires_at ? Date.parse(record.expires_at) : null;
  if (!Number.isFinite(effective) || (expires !== null && !Number.isFinite(expires))) return 'unknown';
  if (effective > now) return 'scheduled';
  if (expires !== null && expires <= now) return 'expired';
  if (!record.channels.length) return 'no_channel';
  return 'authorized';
}

export function createKnowledgeTextFile(title: string, body: string): File {
  if (!body.trim()) throw new Error('EMPTY_KNOWLEDGE_BODY');
  const name = title.trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 100) || 'knowledge';
  // Preserve the original body, including whitespace and Markdown formatting.
  return new File([body], `${name.replace(/\.(md|txt)$/i, '')}.md`, { type: 'text/markdown' });
}
