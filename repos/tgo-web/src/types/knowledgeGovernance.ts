export type KnowledgeDocumentType = 'product' | 'after_sales' | 'faq' | 'sop';

export type KnowledgeChannel = 'wecom_kf' | 'web' | 'app' | 'phone' | 'internal';

export type KnowledgeReviewStatus =
  | 'draft'
  | 'pending_review'
  | 'approved'
  | 'rejected'
  | 'revoked';

export type KnowledgeSourceOrigin = 'internal' | 'customer' | 'website';

export interface KnowledgeGovernanceDraftRequest {
  document_type: KnowledgeDocumentType;
  product_line: string;
  channels: KnowledgeChannel[];
  effective_at: string;
  expires_at?: string;
  owner: string;
  document_version: string;
  allow_automatic_reply: boolean;
  source_origin: KnowledgeSourceOrigin;
}

export interface KnowledgeGovernanceBackfillRequest {
  collection_id: string;
  document_type: KnowledgeDocumentType;
  product_line: string;
  channels: KnowledgeChannel[];
  effective_at: string;
  owner: string;
  document_version: string;
  source_origin: KnowledgeSourceOrigin;
  dry_run: boolean;
}

export interface KnowledgeGovernanceReviewRequest {
  status: 'approved' | 'rejected' | 'revoked';
}

export interface KnowledgeGovernanceRecord {
  id: string;
  project_id: string;
  file_id: string | null;
  qa_pair_id: string | null;
  collection_id: string;
  source_name: string;
  document_type: KnowledgeDocumentType;
  product_line: string;
  channels: KnowledgeChannel[];
  effective_at: string;
  expires_at: string | null;
  owner: string;
  document_version: string;
  allow_automatic_reply: boolean;
  review_status: KnowledgeReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  source_origin: KnowledgeSourceOrigin;
  content_is_untrusted: boolean;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeGovernancePagination {
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface KnowledgeGovernanceListResponse {
  data: KnowledgeGovernanceRecord[];
  pagination: KnowledgeGovernancePagination;
}

export interface KnowledgeGovernanceBackfillResponse {
  scanned_count: number;
  missing_count: number;
  created_count: number;
  dry_run: boolean;
}
