import type {
  StaffMessageAnalysisBatchRequest,
  StaffMessageAnalysisBatchResponse,
} from '@/types/messageAnalysis';
import type {
  ShadowComparisonsResponse, ShadowPendingReviewQueue, ShadowReviewReceipt,
  ShadowReviewRequest, ShadowReviewSummary,
  ShadowSampleBatch, ShadowSampleBatchList, ShadowSampleCreateRequest,
  ShadowProtocolCreateRequest, ShadowReviewProtocol, ShadowReviewProtocolList,
  ShadowProtocolCandidatePage,
  ShadowProtocolIntegrity,
  ShadowGateAssessment,
  ShadowGateSignoffRequest, ShadowGateDecisionRequest, ShadowGateSignoffReceipt,
  ShadowAdjudicationRequest, ShadowAdjudicationReceipt, ShadowAdjudicationHistory,
} from '@/types/shadowReview';

import { BaseApiService } from './base/BaseApiService';
import { apiClient } from './api';


class MessageAnalysisApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    staffQuery: '/v1/message-analysis/staff/messages/query',
    shadowCases: '/v1/message-analysis/staff/shadow-cases',
    shadowReviews: '/v1/message-analysis/staff/shadow-reviews',
    shadowAdjudications: '/v1/message-analysis/staff/shadow-adjudications',
    shadowReviewSummary: '/v1/message-analysis/staff/shadow-review-summary',
    shadowReviewQueue: '/v1/message-analysis/staff/shadow-review-queue',
    shadowSampleBatches: '/v1/message-analysis/staff/shadow-sample-batches',
    shadowReviewProtocol: '/v1/message-analysis/staff/shadow-review-protocol',
    shadowReviewProtocols: '/v1/message-analysis/staff/shadow-review-protocols',
  } as const;

  async queryStaffMessageAnalyses(
    request: StaffMessageAnalysisBatchRequest,
  ): Promise<StaffMessageAnalysisBatchResponse> {
    return this.post<StaffMessageAnalysisBatchResponse>(
      this.endpoints.staffQuery,
      request,
    );
  }

  async getShadowComparisons(
    visitorId: string, signal?: AbortSignal, focusDraftId?: string | null,
  ): Promise<ShadowComparisonsResponse> {
    const query = new URLSearchParams({ visitor_id: visitorId, limit: '30' });
    if (focusDraftId) query.set('focus_draft_id', focusDraftId);
    return apiClient.get<ShadowComparisonsResponse>(
      `${this.endpoints.shadowCases}?${query}`,
      { signal },
    );
  }

  async submitShadowReview(request: ShadowReviewRequest): Promise<ShadowReviewReceipt> {
    return apiClient.post<ShadowReviewReceipt>(this.endpoints.shadowReviews, request);
  }

  async submitShadowAdjudication(
    request: ShadowAdjudicationRequest,
  ): Promise<ShadowAdjudicationReceipt> {
    return apiClient.post<ShadowAdjudicationReceipt>(
      this.endpoints.shadowAdjudications, request,
    );
  }

  async getShadowAdjudicationHistory(
    batchId: string,
    draftId: string,
    visitorId: string,
    beforeId?: string | null,
    signal?: AbortSignal,
  ): Promise<ShadowAdjudicationHistory> {
    const query = new URLSearchParams({
      batch_id: batchId,
      draft_id: draftId,
      visitor_id: visitorId,
      limit: '20',
    });
    if (beforeId) query.set('before_id', beforeId);
    return apiClient.get<ShadowAdjudicationHistory>(
      `${this.endpoints.shadowAdjudications}?${query}`,
      { signal },
    );
  }

  async getShadowReviewSummary(signal?: AbortSignal): Promise<ShadowReviewSummary> {
    return apiClient.get<ShadowReviewSummary>(
      `${this.endpoints.shadowReviewSummary}?days=7`, { signal },
    );
  }

  async getShadowReviewQueue(
    afterDraftId: string | null,
    signal?: AbortSignal,
  ): Promise<ShadowPendingReviewQueue> {
    const query = new URLSearchParams({ days: '7', limit: '10' });
    if (afterDraftId) query.set('after_draft_id', afterDraftId);
    return apiClient.get<ShadowPendingReviewQueue>(
      `${this.endpoints.shadowReviewQueue}?${query}`, { signal },
    );
  }

  async createShadowSampleBatch(
    request: ShadowSampleCreateRequest,
  ): Promise<ShadowSampleBatch> {
    return apiClient.post<ShadowSampleBatch>(
      this.endpoints.shadowSampleBatches, request,
    );
  }

  async listShadowSampleBatches(signal?: AbortSignal): Promise<ShadowSampleBatchList> {
    return apiClient.get<ShadowSampleBatchList>(
      `${this.endpoints.shadowSampleBatches}?limit=20`, { signal },
    );
  }

  async getShadowSampleBatch(
    batchId: string, signal?: AbortSignal,
  ): Promise<ShadowSampleBatch> {
    return apiClient.get<ShadowSampleBatch>(
      `${this.endpoints.shadowSampleBatches}/${encodeURIComponent(batchId)}`,
      { signal },
    );
  }

  async getShadowReviewProtocol(signal?: AbortSignal): Promise<ShadowReviewProtocol | null> {
    return apiClient.get<ShadowReviewProtocol | null>(
      this.endpoints.shadowReviewProtocol, { signal },
    );
  }

  async listShadowReviewProtocols(signal?: AbortSignal): Promise<ShadowReviewProtocolList> {
    return apiClient.get<ShadowReviewProtocolList>(
      `${this.endpoints.shadowReviewProtocols}?limit=20`, { signal },
    );
  }

  async listShadowProtocolCandidates(
    protocolId: string, afterDraftId: string | null, signal?: AbortSignal,
  ): Promise<ShadowProtocolCandidatePage> {
    const query = new URLSearchParams({ limit: '100' });
    if (afterDraftId) query.set('after_draft_id', afterDraftId);
    return apiClient.get<ShadowProtocolCandidatePage>(
      `${this.endpoints.shadowReviewProtocols}/${encodeURIComponent(protocolId)}/candidates?${query}`,
      { signal },
    );
  }

  async verifyShadowProtocolIntegrity(
    protocolId: string, signal?: AbortSignal,
  ): Promise<ShadowProtocolIntegrity> {
    return apiClient.get<ShadowProtocolIntegrity>(
      `${this.endpoints.shadowReviewProtocols}/${encodeURIComponent(protocolId)}/integrity`,
      { signal },
    );
  }

  async getShadowGateReadiness(
    protocolId: string, signal?: AbortSignal,
  ): Promise<ShadowGateAssessment> {
    return apiClient.get<ShadowGateAssessment>(
      `${this.endpoints.shadowReviewProtocols}/${encodeURIComponent(protocolId)}/readiness`,
      { signal },
    );
  }

  async getShadowGateSignoff(
    protocolId: string, signal?: AbortSignal,
  ): Promise<ShadowGateSignoffReceipt | null> {
    return apiClient.get<ShadowGateSignoffReceipt | null>(
      `${this.endpoints.shadowReviewProtocols}/${encodeURIComponent(protocolId)}/signoff`,
      { signal },
    );
  }

  async requestShadowGateSignoff(
    protocolId: string, request: ShadowGateSignoffRequest,
  ): Promise<ShadowGateSignoffReceipt> {
    return apiClient.post<ShadowGateSignoffReceipt>(
      `${this.endpoints.shadowReviewProtocols}/${encodeURIComponent(protocolId)}/signoff`,
      request,
    );
  }

  async decideShadowGateSignoff(
    protocolId: string, request: ShadowGateDecisionRequest,
  ): Promise<ShadowGateSignoffReceipt> {
    return apiClient.post<ShadowGateSignoffReceipt>(
      `${this.endpoints.shadowReviewProtocols}/${encodeURIComponent(protocolId)}/signoff/decision`,
      request,
    );
  }

  async registerShadowReviewProtocol(
    request: ShadowProtocolCreateRequest,
  ): Promise<ShadowReviewProtocol> {
    return apiClient.post<ShadowReviewProtocol>(
      this.endpoints.shadowReviewProtocol, request,
    );
  }

  async finalizeShadowReviewProtocol(): Promise<ShadowReviewProtocol> {
    return apiClient.post<ShadowReviewProtocol>(
      `${this.endpoints.shadowReviewProtocol}/finalize`, {},
    );
  }
}

export const messageAnalysisApiService = new MessageAnalysisApiService();

export default messageAnalysisApiService;
