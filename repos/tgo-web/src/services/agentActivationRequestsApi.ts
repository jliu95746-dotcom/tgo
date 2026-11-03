import apiClient from './api';
import type { AgentActivationReceipt, AgentActivationRequest } from '@/types/agentActivationRequests';

const path = (agentId: string) => `/v1/ai/agents/${encodeURIComponent(agentId)}/activation-requests`;
export const AgentActivationRequestsApi = {
  submit: (agentId: string, command: AgentActivationRequest, signal?: AbortSignal) => apiClient.post<AgentActivationReceipt>(path(agentId), command, { signal }),
  lookup: (agentId: string, key: string, signal?: AbortSignal) => apiClient.get<AgentActivationReceipt>(`${path(agentId)}/${encodeURIComponent(key)}`, { signal }),
};
