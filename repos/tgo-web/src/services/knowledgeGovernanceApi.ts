import BaseApiService from './base/BaseApiService';
import type {
  KnowledgeGovernanceBackfillRequest,
  KnowledgeGovernanceBackfillResponse,
  KnowledgeGovernanceDraftRequest,
  KnowledgeGovernanceListResponse,
  KnowledgeGovernanceRecord,
  KnowledgeGovernanceReviewRequest,
  KnowledgeReviewStatus,
  KnowledgeChannelUpdateRequest,
} from '@/types';

const BASE_ENDPOINT = '/v1/rag/knowledge-governance';

export class KnowledgeGovernanceApiService extends BaseApiService {
  static async updateChannels(recordId: string, request: KnowledgeChannelUpdateRequest): Promise<KnowledgeGovernanceRecord> {
    const service = new KnowledgeGovernanceApiService();
    return service.patch<KnowledgeGovernanceRecord>(`${BASE_ENDPOINT}/${recordId}/channels`, request);
  }
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    LIST: BASE_ENDPOINT,
    FILE: (fileId: string) => `${BASE_ENDPOINT}/files/${fileId}`,
    QA: (pairId: string) => `${BASE_ENDPOINT}/qa-pairs/${pairId}`,
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
    const records: KnowledgeGovernanceRecord[] = [];
    let response: KnowledgeGovernanceListResponse;
    do {
      query.set('offset', String(records.length));
      response = await service.get<KnowledgeGovernanceListResponse>(
        `${service.endpoints.LIST}?${query.toString()}`,
      );
      records.push(...response.data);
    } while (response.pagination.has_next && response.data.length > 0);
    return { ...response, data: records };
  }

  static async saveQADraft(
    pairId: string,
    request: KnowledgeGovernanceDraftRequest,
  ): Promise<KnowledgeGovernanceRecord> {
    const service = new KnowledgeGovernanceApiService();
    return service.put<KnowledgeGovernanceRecord>(service.endpoints.QA(pairId), request);
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
