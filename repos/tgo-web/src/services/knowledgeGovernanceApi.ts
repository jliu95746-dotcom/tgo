import BaseApiService from './base/BaseApiService';
import type {
  KnowledgeGovernanceBackfillRequest,
  KnowledgeGovernanceBackfillResponse,
  KnowledgeGovernanceDraftRequest,
  KnowledgeGovernanceListResponse,
  KnowledgeGovernanceRecord,
  KnowledgeGovernanceReviewRequest,
  KnowledgeReviewStatus,
} from '@/types';

const BASE_ENDPOINT = '/v1/rag/knowledge-governance';

export class KnowledgeGovernanceApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    LIST: BASE_ENDPOINT,
    FILE: (fileId: string) => `${BASE_ENDPOINT}/files/${fileId}`,
    SUBMIT: (recordId: string) => `${BASE_ENDPOINT}/${recordId}/submit`,
    REVIEW: (recordId: string) => `${BASE_ENDPOINT}/${recordId}/review`,
    BACKFILL: `${BASE_ENDPOINT}/backfill`,
  } as const;

  static async list(
    collectionId: string,
    reviewStatus?: KnowledgeReviewStatus,
  ): Promise<KnowledgeGovernanceListResponse> {
    const service = new KnowledgeGovernanceApiService();
    const query = new URLSearchParams({ collection_id: collectionId, limit: '100' });
    if (reviewStatus) query.set('review_status', reviewStatus);
    return service.get<KnowledgeGovernanceListResponse>(
      `${service.endpoints.LIST}?${query.toString()}`,
    );
  }

  static async saveDraft(
    fileId: string,
    request: KnowledgeGovernanceDraftRequest,
  ): Promise<KnowledgeGovernanceRecord> {
    const service = new KnowledgeGovernanceApiService();
    return service.put<KnowledgeGovernanceRecord>(service.endpoints.FILE(fileId), request);
  }

  static async submit(recordId: string): Promise<KnowledgeGovernanceRecord> {
    const service = new KnowledgeGovernanceApiService();
    return service.post<KnowledgeGovernanceRecord>(service.endpoints.SUBMIT(recordId));
  }

  static async review(
    recordId: string,
    request: KnowledgeGovernanceReviewRequest,
  ): Promise<KnowledgeGovernanceRecord> {
    const service = new KnowledgeGovernanceApiService();
    return service.post<KnowledgeGovernanceRecord>(service.endpoints.REVIEW(recordId), request);
  }

  static async backfill(
    request: KnowledgeGovernanceBackfillRequest,
  ): Promise<KnowledgeGovernanceBackfillResponse> {
    const service = new KnowledgeGovernanceApiService();
    return service.post<KnowledgeGovernanceBackfillResponse>(
      service.endpoints.BACKFILL,
      request,
    );
  }
}
