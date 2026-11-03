import apiClient from './api';
import type { KnowledgeReviewCase, KnowledgeReviewRequest, KnowledgeReviewResponse, KnowledgeReviewState } from '@/types/agentTrialReviews';

const path = (agentId: string, reportId: string) =>
  `/v1/ai/agents/${encodeURIComponent(agentId)}/trials/${encodeURIComponent(reportId)}/reviews`;

export const AgentTrialReviewsApi = {
  cases: (signal?: AbortSignal) => apiClient.get<KnowledgeReviewCase[]>('/v1/business-scenarios/knowledge-review-cases', { signal }),
  state: (agentId: string, reportId: string, signal?: AbortSignal) => apiClient.get<KnowledgeReviewState>(path(agentId, reportId), { signal }),
  submit: (agentId: string, reportId: string, request: KnowledgeReviewRequest, signal?: AbortSignal) =>
    apiClient.post<KnowledgeReviewResponse>(path(agentId, reportId), request, { signal }),
};
