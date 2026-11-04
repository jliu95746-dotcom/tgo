import { apiClient } from './api';
import type {
  HiddenShadowCasePage, HiddenShadowIntegrity, HiddenShadowProtocol,
  HiddenShadowProtocolCreateRequest, HiddenShadowProtocolList,
  HiddenShadowCaseDetail, HiddenShadowReviewRequest,
  HiddenShadowSourceMessage,
  HiddenShadowReviewReceipt, HiddenShadowReviewSummary,
  HiddenShadowReviewPair, HiddenShadowAdjudicationRequest,
  HiddenShadowAdjudicationReceipt,
  HiddenShadowGateAssessment, HiddenShadowGateSignoffRequest,
  HiddenShadowGateDecisionRequest, HiddenShadowGateSignoffReceipt,
} from '@/types/hiddenShadow';

const base = '/v1/message-analysis/staff/hidden-shadow-protocols';

export const hiddenShadowApi = {
  list(signal?: AbortSignal): Promise<HiddenShadowProtocolList> {
    return apiClient.get<HiddenShadowProtocolList>(`${base}?limit=20`, { signal });
  },
  register(request: HiddenShadowProtocolCreateRequest): Promise<HiddenShadowProtocol> {
    return apiClient.post<HiddenShadowProtocol>(base, request);
  },
  finalize(protocolId: string): Promise<HiddenShadowProtocol> {
    return apiClient.post<HiddenShadowProtocol>(
      `${base}/${encodeURIComponent(protocolId)}/finalize`, {},
    );
  },
  cancel(protocolId: string): Promise<HiddenShadowProtocol> {
    return apiClient.post<HiddenShadowProtocol>(
      `${base}/${encodeURIComponent(protocolId)}/cancel`, {},
    );
  },
  cases(protocolId: string, afterCandidateId: string | null, signal?: AbortSignal): Promise<HiddenShadowCasePage> {
    const query = new URLSearchParams({ limit: '100', selected_only: 'true' });
    if (afterCandidateId) query.set('after_candidate_id', afterCandidateId);
    return apiClient.get<HiddenShadowCasePage>(
      `${base}/${encodeURIComponent(protocolId)}/cases?${query}`, { signal },
    );
  },
  integrity(protocolId: string, signal?: AbortSignal): Promise<HiddenShadowIntegrity> {
    return apiClient.get<HiddenShadowIntegrity>(
      `${base}/${encodeURIComponent(protocolId)}/integrity`, { signal },
    );
  },
  caseDetail(protocolId: string, candidateId: string, signal?: AbortSignal): Promise<HiddenShadowCaseDetail> {
    return apiClient.get<HiddenShadowCaseDetail>(
      `${base}/${encodeURIComponent(protocolId)}/cases/${encodeURIComponent(candidateId)}`,
      { signal },
    );
  },
  sourceMessage(protocolId: string, candidateId: string, signal?: AbortSignal): Promise<HiddenShadowSourceMessage> {
    return apiClient.get<HiddenShadowSourceMessage>(
      `${base}/${encodeURIComponent(protocolId)}/cases/${encodeURIComponent(candidateId)}/source-message`,
      { signal },
    );
  },
  myReview(protocolId: string, candidateId: string, signal?: AbortSignal): Promise<HiddenShadowReviewReceipt | null> {
    return apiClient.get<HiddenShadowReviewReceipt | null>(
      `${base}/${encodeURIComponent(protocolId)}/cases/${encodeURIComponent(candidateId)}/my-review`,
      { signal },
    );
  },
  submitReview(protocolId: string, candidateId: string, request: HiddenShadowReviewRequest): Promise<HiddenShadowReviewReceipt> {
    return apiClient.post<HiddenShadowReviewReceipt>(
      `${base}/${encodeURIComponent(protocolId)}/cases/${encodeURIComponent(candidateId)}/reviews`,
      request,
    );
  },
  reviewSummary(protocolId: string, signal?: AbortSignal): Promise<HiddenShadowReviewSummary> {
    return apiClient.get<HiddenShadowReviewSummary>(
      `${base}/${encodeURIComponent(protocolId)}/review-summary`, { signal },
    );
  },
  reviewPair(protocolId: string, candidateId: string, signal?: AbortSignal): Promise<HiddenShadowReviewPair> {
    return apiClient.get<HiddenShadowReviewPair>(
      `${base}/${encodeURIComponent(protocolId)}/cases/${encodeURIComponent(candidateId)}/reviews`,
      { signal },
    );
  },
  submitAdjudication(
    protocolId: string, candidateId: string, request: HiddenShadowAdjudicationRequest,
  ): Promise<HiddenShadowAdjudicationReceipt> {
    return apiClient.post<HiddenShadowAdjudicationReceipt>(
      `${base}/${encodeURIComponent(protocolId)}/cases/${encodeURIComponent(candidateId)}/adjudication`,
      request,
    );
  },
  gateReadiness(protocolId: string, signal?: AbortSignal): Promise<HiddenShadowGateAssessment> {
    return apiClient.get<HiddenShadowGateAssessment>(
      `${base}/${encodeURIComponent(protocolId)}/gate-readiness`, { signal },
    );
  },
  signoff(protocolId: string, signal?: AbortSignal): Promise<HiddenShadowGateSignoffReceipt | null> {
    return apiClient.get<HiddenShadowGateSignoffReceipt | null>(
      `${base}/${encodeURIComponent(protocolId)}/signoff`, { signal },
    );
  },
  requestSignoff(
    protocolId: string, request: HiddenShadowGateSignoffRequest,
  ): Promise<HiddenShadowGateSignoffReceipt> {
    return apiClient.post<HiddenShadowGateSignoffReceipt>(
      `${base}/${encodeURIComponent(protocolId)}/signoff`, request,
    );
  },
  decideSignoff(
    protocolId: string, request: HiddenShadowGateDecisionRequest,
  ): Promise<HiddenShadowGateSignoffReceipt> {
    return apiClient.post<HiddenShadowGateSignoffReceipt>(
      `${base}/${encodeURIComponent(protocolId)}/signoff/decision`, request,
    );
  },
};
