import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { AgentDeviceScriptRunsApi } from '@/services/agentDeviceScriptRunsApi';
import { APIError } from '@/services/api';
import { validRunPointer, validScriptRun } from '@/utils/agentDeviceScriptRunValidation';
import type { ScriptRunControl, ScriptRunMetadata, ScriptRunView } from '@/types/agentDeviceScriptRuns';
import type { ScriptBusinessValues } from '@/types/agentDeviceScripts';

type ErrorKind = 'network' | 'storage' | 'invalidResponse' | 'forbidden';
interface State {
  identity: string | null; agentId: string | null;
  pointer: ScriptRunControl | null; receipt: ScriptRunMetadata | null;
  storageReady: boolean; starting: boolean; querying: boolean; cancelling: boolean;
  error: ErrorKind | null;
  select: (agent: string) => Promise<void>;
  reset: () => void;
  start: (report: string, review: string, parameters: ScriptBusinessValues) => Promise<ScriptRunView | null>;
  recover: () => Promise<ScriptRunView | null>;
  cancel: () => Promise<ScriptRunView | null>;
}
const empty = { identity: null, agentId: null, pointer: null, receipt: null,
  storageReady: false, starting: false, querying: false, cancelling: false, error: null };
const identity = () => { const user = useAuthStore.getState().user; return user?.role === 'admin' ? `${user.project_id}:${user.id}` : null; };
const key = (owner: string, agent: string) => `tgo:employee-script-run:v1:${owner}:${agent}`;
const terminal = (receipt: ScriptRunMetadata | null) => Boolean(receipt && ['succeeded', 'failed', 'cancelled'].includes(receipt.status));
let generation = 0, observation = 0;
const controllers = new Set<AbortController>();

export const useAgentDeviceScriptRunStore = create<State>((set, get) => {
  const current = (epoch: number, owner: string | null, agent: string | null) =>
    epoch === generation && owner !== null && owner === identity() && get().identity === owner && get().agentId === agent;
  const write = (owner: string, agent: string, pointer: ScriptRunControl) => {
    try {
      const raw = JSON.stringify(pointer);
      sessionStorage.setItem(key(owner, agent), raw);
      return sessionStorage.getItem(key(owner, agent)) === raw;
    } catch { return false; }
  };
  const perform = async (action: 'start' | 'read' | 'cancel', body?: { report: string; review: string; parameters: ScriptBusinessValues }): Promise<ScriptRunView | null> => {
    const state = get(), user = useAuthStore.getState().user, epoch = generation;
    const { identity: owner, agentId: agent } = state;
    if (!owner || !agent || !user || !current(epoch, owner, agent) || !state.storageReady) return null;
    if (action === 'start' && (!body || state.starting || state.querying || state.cancelling
      || state.pointer && !terminal(state.receipt))) return null;
    if (action === 'read' && state.querying || action === 'cancel' && state.cancelling) return null;
    let pointer = state.pointer;
    if (action === 'start' && body) {
      pointer = { request_id: crypto.randomUUID(), report_id: body.report, review_id: body.review };
      if (!validRunPointer(pointer)) { set({ error: 'invalidResponse' }); return null; }
      if (!write(owner, agent, pointer)) { set({ error: 'storage' }); return null; }
      set({ pointer, receipt: null });
    }
    if (!pointer) return null;
    const observer = ++observation;
    const controller = new AbortController();
    controllers.add(controller);
    const flag = action === 'start' ? 'starting' : action === 'read' ? 'querying' : 'cancelling';
    set({ [flag]: true, error: null });
    try {
      let response: ScriptRunView;
      if (action === 'start' && body) {
        response = await AgentDeviceScriptRunsApi.start(agent, { ...pointer, parameters: { ...body.parameters } }, controller.signal);
      } else if (action === 'cancel') {
        response = await AgentDeviceScriptRunsApi.cancel(agent, pointer, controller.signal);
      } else {
        response = await AgentDeviceScriptRunsApi.read(agent, pointer.request_id, controller.signal);
      }
      if (!current(epoch, owner, agent) || observer !== observation) return null;
      if (!validScriptRun(response, user.project_id, user.id, agent, pointer)) throw Error('invalidResponse');
      if (terminal(get().receipt) && !terminal(response)) return null;
      const { result_fields, ...metadata } = response;
      set({ receipt: metadata });
      return { ...metadata, result_fields };
    } catch (error) {
      if (current(epoch, owner, agent) && observer === observation) set({ error: error instanceof APIError
        && [401, 403].includes(error.status) ? 'forbidden'
        : error instanceof Error && error.message === 'invalidResponse' ? 'invalidResponse' : 'network' });
      return null;
    } finally {
      controllers.delete(controller);
      if (current(epoch, owner, agent)) set({ [flag]: false });
    }
  };
  return {
    ...empty,
    reset() { generation++; observation++; controllers.forEach(controller => controller.abort()); controllers.clear(); set(empty); },
    async select(agent) {
      const owner = identity();
      if (owner && get().identity === owner && get().agentId === agent && get().storageReady) return;
      get().reset();
      if (!owner) return;
      set({ identity: owner, agentId: agent });
      try {
        const raw = sessionStorage.getItem(key(owner, agent));
        const pointer: unknown = raw ? JSON.parse(raw) : null;
        if (pointer !== null && !validRunPointer(pointer)) throw Error('invalidResponse');
        set({ pointer: pointer as ScriptRunControl | null, storageReady: true });
      } catch { set({ error: 'storage' }); return; }
      if (get().pointer) await get().recover();
    },
    start: (report, review, parameters) => perform('start', { report, review, parameters }),
    recover: () => perform('read'),
    cancel: () => perform('cancel'),
  };
});
useAuthStore.subscribe(() => {
  if (useAgentDeviceScriptRunStore.getState().identity !== identity()) useAgentDeviceScriptRunStore.getState().reset();
});
