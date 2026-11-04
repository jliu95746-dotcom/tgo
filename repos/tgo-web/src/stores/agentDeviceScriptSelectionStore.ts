import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAgentActivationStore } from './agentActivationStore';
import { AgentDeviceScriptSelectionApi } from '@/services/agentDeviceScriptSelectionApi';
import { APIError } from '@/services/api';
import { validSelectionRecord, validSelectionState, validSelectionWrite } from '@/utils/agentDeviceScriptSelectionValidation';
import type { ScriptSelectionRecord, ScriptSelectionState, ScriptSelectionWrite } from '@/types/agentDeviceScriptSelection';

type ErrorKind = 'forbidden' | 'network' | 'invalidResponse' | 'storage' | 'notFound' | 'conflict';
interface State {
  identity: string | null; agentId: string | null; current: ScriptSelectionState | null;
  pending: ScriptSelectionWrite | null; loading: boolean; busy: boolean; error: ErrorKind | null;
  select: (agent: string) => Promise<void>; refresh: () => Promise<void>;
  choose: (report: string, review: string) => Promise<void>; clear: () => Promise<void>;
  recover: () => Promise<void>; retry: () => Promise<void>; reset: () => void;
}
const empty = { identity: null, agentId: null, current: null, pending: null, loading: false, busy: false, error: null };
const scope = () => { const user = useAuthStore.getState().user; return user?.role === 'admin' ? `${user.project_id}:${user.id}` : null; };
const key = (identity: string, agent: string) => `tgo:script-selection:v1:${identity}:${agent}`;
let generation = 0;
let operation: AbortController | null = null;
const invalid = () => Error('invalidResponse');
const errorKind = (error: unknown): ErrorKind => error instanceof APIError && [401, 403].includes(error.status) ? 'forbidden'
  : error instanceof APIError && error.status === 404 ? 'notFound'
    : error instanceof APIError && error.status === 409 ? 'conflict'
      : error instanceof Error && error.message === 'invalidResponse' ? 'invalidResponse' : 'network';

export const useAgentDeviceScriptSelectionStore = create<State>((set, get) => {
  const owned = (epoch: number, identity: string, agent: string) => epoch === generation && identity === scope()
    && get().identity === identity && get().agentId === agent;
  const persist = (pending: ScriptSelectionWrite | null) => {
    const { identity, agentId } = get();
    if (!identity || !agentId) return false;
    try {
      const encoded = JSON.stringify(pending);
      sessionStorage.setItem(key(identity, agentId), encoded);
      return sessionStorage.getItem(key(identity, agentId)) === encoded;
    } catch { return false; }
  };
  const observe = async (epoch: number, identity: string, agent: string, controller: AbortController) => {
    const [project, actor] = identity.split(':');
    const value = await AgentDeviceScriptSelectionApi.read(agent, controller.signal);
    if (!owned(epoch, identity, agent)) return;
    if (!validSelectionState(value, project, actor, agent)) throw invalid();
    set({ current: value });
  };
  const accept = (value: ScriptSelectionRecord, pointer: ScriptSelectionWrite, identity: string, agent: string) => {
    const [project, actor] = identity.split(':');
    if (!validSelectionRecord(value, project, agent) || value.configured_by !== actor
      || value.request_id !== pointer.request_id || value.revision !== pointer.expected_revision + 1 || value.enabled !== pointer.enabled
      || pointer.enabled && (value.report_id !== pointer.report_id || value.review_id !== pointer.review_id)) throw invalid();
    set({ pending: null, current: null });
    if (!persist(null)) set({ error: 'storage' });
    useAgentActivationStore.getState().invalidate(agent);
  };
  const submit = async () => {
    const { identity, agentId: agent, pending } = get();
    if (!identity || !agent || !pending || get().busy || get().loading) return;
    const epoch = generation, controller = new AbortController(), command = { ...pending };
    operation = controller; set({ busy: true, error: null });
    try {
      const value = await AgentDeviceScriptSelectionApi.save(agent, command, controller.signal);
      if (!owned(epoch, identity, agent)) return;
      accept(value, command, identity, agent);
      await observe(epoch, identity, agent, controller);
    } catch (error) {
      if (owned(epoch, identity, agent)) {
        const kind = errorKind(error);
        // This configuration service rolls back rejected 409 transactions.
        // An unknown transport outcome retains its original request instead.
        if (kind === 'conflict') {
          set({ pending: null, current: null }); persist(null);
          try { await observe(epoch, identity, agent, controller); } catch { /* Keep conflict visible. */ }
        }
        if (owned(epoch, identity, agent)) set({ error: kind });
      }
    } finally {
      if (owned(epoch, identity, agent)) set({ busy: false });
      if (operation === controller) operation = null;
    }
  };
  const begin = async (report: string | null, review: string | null) => {
    const { current, pending, busy, loading } = get();
    if (!current || pending || busy || loading) return;
    const command: ScriptSelectionWrite = { request_id: crypto.randomUUID(), expected_revision: current.selection?.revision ?? 0,
      enabled: report !== null, report_id: report, review_id: review };
    if (!validSelectionWrite(command)) { set({ error: 'invalidResponse' }); return; }
    set({ pending: command });
    if (!persist(command)) { set({ pending: null, error: 'storage' }); return; }
    await submit();
  };
  return {
    ...empty,
    reset: () => { generation++; operation?.abort(); operation = null; set(empty); },
    select: async agent => {
      const identity = scope();
      if (identity && get().identity === identity && get().agentId === agent) return;
      get().reset();
      if (!identity) { set({ error: 'forbidden' }); return; }
      set({ identity, agentId: agent });
      try {
        const raw = sessionStorage.getItem(key(identity, agent));
        const pointer: unknown = raw ? JSON.parse(raw) : null;
        if (pointer !== null && !validSelectionWrite(pointer)) throw invalid();
        set({ pending: pointer });
      } catch { set({ error: 'storage' }); return; }
      if (get().pending) await get().recover(); else await get().refresh();
    },
    refresh: async () => {
      const { identity, agentId: agent, busy, loading } = get();
      if (!identity || !agent || busy || loading) return;
      const epoch = generation, controller = new AbortController(); operation = controller;
      set({ loading: true, error: null });
      try { await observe(epoch, identity, agent, controller); }
      catch (error) { if (owned(epoch, identity, agent)) set({ current: null, error: errorKind(error) }); }
      finally { if (owned(epoch, identity, agent)) set({ loading: false }); if (operation === controller) operation = null; }
    },
    choose: async (report, review) => begin(report, review),
    clear: async () => begin(null, null),
    retry: async () => { if (get().error === 'notFound') await submit(); },
    recover: async () => {
      const { identity, agentId: agent, pending, busy, loading } = get();
      if (!identity || !agent || !pending || busy || loading) return;
      const epoch = generation, controller = new AbortController(); operation = controller;
      set({ busy: true, error: null });
      try {
        const value = await AgentDeviceScriptSelectionApi.original(agent, pending.request_id, controller.signal);
        if (!owned(epoch, identity, agent)) return;
        accept(value, pending, identity, agent);
        await observe(epoch, identity, agent, controller);
      } catch (error) { if (owned(epoch, identity, agent)) set({ error: errorKind(error) }); }
      finally { if (owned(epoch, identity, agent)) set({ busy: false }); if (operation === controller) operation = null; }
    },
  };
});

useAuthStore.subscribe(() => {
  if (useAgentDeviceScriptSelectionStore.getState().identity !== scope()) useAgentDeviceScriptSelectionStore.getState().reset();
});
