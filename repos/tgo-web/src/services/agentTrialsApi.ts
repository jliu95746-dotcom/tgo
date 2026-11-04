import apiClient from './api';
import type { AgentTrialReport, AgentTrialRequest } from '@/types/agentTrials';

const path = (agentId: string) => `/v1/ai/agents/${encodeURIComponent(agentId)}/trials`;

/** Login scope and billing are determined by the gateway, never by page fields. */
export const AgentTrialsApi = {
  start: (agentId: string, request: AgentTrialRequest, signal?: AbortSignal) =>
    apiClient.post<AgentTrialReport>(path(agentId), request, { signal }),
  lookup: (agentId: string, request: AgentTrialRequest, signal?: AbortSignal) =>
    apiClient.post<AgentTrialReport>(`${path(agentId)}/lookup`, request, { signal }),
  byRequest: (agentId: string, requestKey: string, signal?: AbortSignal) =>
    apiClient.get<AgentTrialReport>(`${path(agentId)}/requests/${encodeURIComponent(requestKey)}`, { signal }),
  get: (agentId: string, reportId: string, signal?: AbortSignal) =>
    apiClient.get<AgentTrialReport>(`${path(agentId)}/${encodeURIComponent(reportId)}`, { signal }),
  list: (agentId: string, signal?: AbortSignal) =>
    apiClient.get<AgentTrialReport[]>(`${path(agentId)}?limit=20`, { signal }),
  cancel: (agentId: string, reportId: string, signal?: AbortSignal) =>
    apiClient.post<AgentTrialReport>(`${path(agentId)}/${encodeURIComponent(reportId)}/cancel`, undefined, { signal }),
};
