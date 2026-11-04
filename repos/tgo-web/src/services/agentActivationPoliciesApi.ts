import apiClient from './api';
import type { AgentActivationPolicies, AgentActivationPolicyChange, AgentActivationPolicyRecord } from '@/types/agentActivationPolicies';
import type { KnowledgeChannel } from '@/types/scenarioResources';
import type { AgentActivationReadiness } from '@/types/agentActivationReadiness';

const path = (agentId: string) => `/v1/ai/agents/${encodeURIComponent(agentId)}/activation-policies`;

export const AgentActivationPoliciesApi = {
  readiness: (agentId: string, signal?: AbortSignal) => apiClient.get<AgentActivationReadiness>(
    `/v1/ai/agents/${encodeURIComponent(agentId)}/activation-readiness`,
    { signal },
  ),
  read: (agentId: string, signal?: AbortSignal) => apiClient.get<AgentActivationPolicies>(path(agentId), { signal }),
  save: (agentId: string, command: AgentActivationPolicyChange, signal?: AbortSignal) => apiClient.put<AgentActivationPolicyRecord>(
    `${path(agentId)}/${command.selection.channel}`, command, { signal },
  ),
  remove: (agentId: string, channel: KnowledgeChannel, revision: number, signal?: AbortSignal) => apiClient.delete<AgentActivationPolicyRecord>(
    `${path(agentId)}/${channel}?expected_revision=${encodeURIComponent(revision)}`,
    { signal },
  ),
};
