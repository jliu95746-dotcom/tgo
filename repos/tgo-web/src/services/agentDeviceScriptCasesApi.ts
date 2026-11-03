import apiClient from './api';
import type { AgentScriptCaseWrite, AgentScriptCaseRecord, AgentScriptCasePage,
  AgentScriptCaseRead, AgentScriptCaseRun, AgentScriptEvidence,
  ScriptCaseSaveControl, ScriptCaseSaveState } from '@/types/agentDeviceScriptCases';
import type { ScriptTrialRequestControl } from '@/types/agentDeviceScripts';

const root = (agent: string) => `/v1/ai/agents/${encodeURIComponent(agent)}`;
const version = (agent: string, id: string) => `${root(agent)}/device-script-cases/versions/${encodeURIComponent(id)}`;
// Private bodies must not be served from browser cache after expiry or logout.
const privateRead = (signal?: AbortSignal) => ({ signal, cache: 'no-store' as RequestCache });
export const AgentDeviceScriptCasesApi = {
  list: (agent: string, skip = 0, signal?: AbortSignal) =>
    apiClient.get<AgentScriptCasePage>(`${root(agent)}/device-script-cases?skip=${skip}&limit=20`, privateRead(signal)),
  save: (agent: string, body: AgentScriptCaseWrite, signal?: AbortSignal) =>
    apiClient.post<AgentScriptCaseRecord>(`${root(agent)}/device-script-cases`, body, { signal }),
  recover: (agent: string, request: string, signal?: AbortSignal) =>
    apiClient.get<AgentScriptCaseRecord>(`${root(agent)}/device-script-cases/requests/${encodeURIComponent(request)}`, privateRead(signal)),
  saveState: (agent: string, request: string, signal?: AbortSignal) =>
    apiClient.get<ScriptCaseSaveState>(`${root(agent)}/device-script-cases/requests/${encodeURIComponent(request)}/state`, privateRead(signal)),
  cancelSave: (agent: string, body: ScriptCaseSaveControl, signal?: AbortSignal) =>
    apiClient.post<ScriptCaseSaveState>(`${root(agent)}/device-script-cases/requests/${encodeURIComponent(body.request_id)}/cancel`, body, { signal }),
  read: (agent: string, id: string, signal?: AbortSignal) =>
    apiClient.get<AgentScriptCaseRead>(version(agent, id), privateRead(signal)),
  run: (agent: string, id: string, body: ScriptTrialRequestControl, signal?: AbortSignal) =>
    apiClient.post<AgentScriptCaseRun>(`${version(agent, id)}/trials`, body, { signal }),
  evidence: (agent: string, report: string, signal?: AbortSignal) =>
    apiClient.get<AgentScriptEvidence>(`${root(agent)}/device-script-trials/${encodeURIComponent(report)}/evidence`, privateRead(signal)),
};
