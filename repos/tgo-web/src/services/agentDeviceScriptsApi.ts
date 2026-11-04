import apiClient from './api';
import type {
  AgentScriptBinding, AgentScriptBindingState, AgentScriptBindingWrite,
  AgentScriptTrialReport, AgentScriptTrialStart,
  ScriptTrialRequestControl, ScriptTrialRequestState,
} from '@/types/agentDeviceScripts';

const root = (agent: string) => `/v1/ai/agents/${encodeURIComponent(agent)}`;
export const AgentDeviceScriptsApi = {
  readBinding: (agent: string, signal?: AbortSignal) =>
    apiClient.get<AgentScriptBindingState>(`${root(agent)}/device-script-binding`, { signal }),
  saveBinding: (agent: string, body: AgentScriptBindingWrite, signal?: AbortSignal) =>
    apiClient.put<AgentScriptBinding>(`${root(agent)}/device-script-binding`, body, { signal }),
  recoverBinding: (agent: string, request: string, signal?: AbortSignal) =>
    apiClient.get<AgentScriptBinding>(`${root(agent)}/device-script-binding/requests/${encodeURIComponent(request)}`, { signal }),
  start: (agent: string, body: AgentScriptTrialStart, signal?: AbortSignal) =>
    apiClient.post<AgentScriptTrialReport>(`${root(agent)}/device-script-trials`, body, { signal }),
  recover: (agent: string, request: string, signal?: AbortSignal) =>
    apiClient.get<AgentScriptTrialReport>(`${root(agent)}/device-script-trials/requests/${encodeURIComponent(request)}`, { signal }),
  requestState: (agent: string, request: string, signal?: AbortSignal) =>
    apiClient.get<ScriptTrialRequestState>(`${root(agent)}/device-script-trials/requests/${encodeURIComponent(request)}/state`, { signal }),
  cancelRequest: (agent: string, body: ScriptTrialRequestControl, signal?: AbortSignal) =>
    apiClient.post<ScriptTrialRequestState>(`${root(agent)}/device-script-trials/requests/${encodeURIComponent(body.request_id)}/cancel`, body, { signal }),
};
