import apiClient from './api';
import type { ScriptSelectionRecord, ScriptSelectionState, ScriptSelectionWrite } from '@/types/agentDeviceScriptSelection';

const root = (agent: string) => `/v1/ai/agents/${encodeURIComponent(agent)}/device-script-selection`;
export const AgentDeviceScriptSelectionApi = {
  read: (agent: string, signal: AbortSignal) =>
    apiClient.get<ScriptSelectionState>(root(agent), { signal, cache: 'no-store' }),
  save: (agent: string, command: ScriptSelectionWrite, signal: AbortSignal) =>
    apiClient.post<ScriptSelectionRecord>(root(agent), command, { signal }),
  original: (agent: string, request: string, signal: AbortSignal) =>
    apiClient.get<ScriptSelectionRecord>(`${root(agent)}/requests/${encodeURIComponent(request)}`, { signal, cache: 'no-store' }),
};
