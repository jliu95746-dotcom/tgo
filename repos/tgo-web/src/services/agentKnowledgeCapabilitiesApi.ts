import apiClient from './api';
import type { AgentKnowledgeCapabilities } from '@/types/agentKnowledgeCapabilities';
import type { KnowledgeChannel } from '@/types/knowledgeGovernance';

export const AgentKnowledgeCapabilitiesApi = {
  read: (agentId: string, channel: KnowledgeChannel, signal?: AbortSignal) => apiClient.get<AgentKnowledgeCapabilities>(
    `/v1/ai/agents/${encodeURIComponent(agentId)}/knowledge-capabilities?${new URLSearchParams({ channel })}`, { signal },
  ),
};
