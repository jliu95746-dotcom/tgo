import apiClient from './api';
import type { KnowledgeCatalog, KnowledgeCatalogWrite, KnowledgeReviewRequest, KnowledgeReviewResponse, KnowledgeReviewState } from '@/types/agentTrialReviews';

const path = (agentId: string, reportId: string) =>
  `/v1/ai/agents/${encodeURIComponent(agentId)}/trials/${encodeURIComponent(reportId)}/reviews`;

export const AgentTrialReviewsApi = {
  catalog: (agentId: string, signal?: AbortSignal) =>
    apiClient.get<KnowledgeCatalog>(`/v1/ai/agents/${encodeURIComponent(agentId)}/knowledge-test-catalog`, { signal }),
  saveCatalog: (agentId: string, request: KnowledgeCatalogWrite, signal?: AbortSignal) =>
    apiClient.post<KnowledgeCatalog>(`/v1/ai/agents/${encodeURIComponent(agentId)}/knowledge-test-catalog`, request, { signal }),
  cases: async (agentId: string, signal?: AbortSignal) =>
    (await AgentTrialReviewsApi.catalog(agentId, signal)).cases,
  state: (agentId: string, reportId: string, signal?: AbortSignal) => apiClient.get<KnowledgeReviewState>(path(agentId, reportId), { signal }),
  submit: (agentId: string, reportId: string, request: KnowledgeReviewRequest, signal?: AbortSignal) =>
    apiClient.post<KnowledgeReviewResponse>(path(agentId, reportId), request, { signal }),
};
