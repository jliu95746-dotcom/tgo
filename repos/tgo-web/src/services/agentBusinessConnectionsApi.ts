import apiClient from './api';
import type { AgentBusinessConnection, AgentBusinessConnections, AgentBusinessConnectionWrite } from '@/types/agentBusinessConnections';

const path = (agentId: string) => `/v1/ai/agents/${encodeURIComponent(agentId)}/business-connections`;
export const AgentBusinessConnectionsApi = {
  read: (agentId: string) => apiClient.get<AgentBusinessConnections>(path(agentId)),
  save: (agentId: string, connectionId: string, body: AgentBusinessConnectionWrite) =>
    apiClient.put<AgentBusinessConnection>(`${path(agentId)}/${encodeURIComponent(connectionId)}`, body),
  recover: (agentId: string, requestId: string) =>
    apiClient.get<AgentBusinessConnection>(`${path(agentId)}/requests/${encodeURIComponent(requestId)}`),
};
