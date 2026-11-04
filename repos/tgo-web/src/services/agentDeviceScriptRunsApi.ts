import apiClient from './api';
import type { ScriptRunControl, ScriptRunStart, ScriptRunView } from '@/types/agentDeviceScriptRuns';

const root = (agent: string) => `/v1/ai/agents/${encodeURIComponent(agent)}/device-script-runs`;
const original = (agent: string, request: string) => `${root(agent)}/requests/${encodeURIComponent(request)}`;

export const AgentDeviceScriptRunsApi = {
  start: (agent: string, command: ScriptRunStart, signal: AbortSignal) =>
    apiClient.post<ScriptRunView>(root(agent), command, { signal }),
  read: (agent: string, request: string, signal: AbortSignal) =>
    apiClient.get<ScriptRunView>(original(agent, request), { signal, cache: 'no-store' }),
  cancel: (agent: string, command: ScriptRunControl, signal: AbortSignal) =>
    apiClient.post<ScriptRunView>(`${original(agent, command.request_id)}/cancel`, command, { signal }),
};
