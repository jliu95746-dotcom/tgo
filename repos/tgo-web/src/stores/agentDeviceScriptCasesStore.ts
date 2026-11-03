import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAgentDeviceScriptStore } from './agentDeviceScriptStore';
import { useAgentBusinessCurrentStateStore } from './agentBusinessCurrentStateStore';
import { AgentDeviceScriptCasesApi } from '@/services/agentDeviceScriptCasesApi';
import { APIError } from '@/services/api';
import { scriptUuid } from '@/utils/agentDeviceScriptValidation';
import { validCaseSavePointer, validCaseSaveState, validScriptCase } from '@/utils/agentDeviceScriptCaseValidation';
import type { AgentScriptCaseRecord, ScriptCaseSavePointer, ScriptCaseSaveState } from '@/types/agentDeviceScriptCases';
import type { ScriptBusinessValues } from '@/types/agentDeviceScripts';

interface State {
  identity: string | null; agentId: string | null; items: AgentScriptCaseRecord[];
  pending: ScriptCaseSavePointer | null; lastSaved: AgentScriptCaseRecord | null;
  saveState: ScriptCaseSaveState | null; cancelling: boolean;
  hasMore: boolean; loading: boolean; busy: boolean; storageReady: boolean;
  error: 'network' | 'invalidResponse' | 'storage' | 'active' | 'configuration' | 'forbidden' | null;
  select: (agent: string) => Promise<void>; reset: () => void;
  refresh: (more?: boolean) => Promise<void>; recover: () => Promise<void>;
  cancel: () => Promise<void>;
  save: (title: string, parameters: ScriptBusinessValues, expected: ScriptBusinessValues, editing?: AgentScriptCaseRecord | null) => Promise<boolean>;
}
const initial = { identity: null, agentId: null, items: [], pending: null, lastSaved: null,
  saveState: null, cancelling: false, hasMore: false, loading: false, busy: false, storageReady: false, error: null };
const identity = () => { const user = useAuthStore.getState().user; return user?.role === 'admin' ? `${user.project_id}:${user.id}` : null; };
const key = (owner: string, agent: string) => `tgo:employee-script-case:v1:${owner}:${agent}`;
const invalid = () => Error('invalidResponse');
const kind = (error: unknown): State['error'] => error instanceof APIError && [401, 403].includes(error.status) ? 'forbidden'
  : error instanceof Error && error.message === 'invalidResponse' ? 'invalidResponse' : 'network';
let epoch = 0, listEpoch = 0, operation = 0;
let controller: AbortController | null = null, listController: AbortController | null = null;

export const useAgentDeviceScriptCasesStore = create<State>((set, get) => {
  const current = (stamp: number, owner: string | null, agent: string | null) => Boolean(owner && agent
    && epoch === stamp && identity() === owner && get().identity === owner && get().agentId === agent);
  const writePointer = (owner: string, agent: string, pending: ScriptCaseSavePointer | null) => {
    try { const raw = JSON.stringify({ pending }); sessionStorage.setItem(key(owner, agent), raw);
      return sessionStorage.getItem(key(owner, agent)) === raw;
    } catch { return false; }
  };
  const accept = (value: AgentScriptCaseRecord, pointer: ScriptCaseSavePointer, project: string, actor: string, agent: string, owner: string) => {
    if (!validScriptCase(value, project, agent) || value.created_by !== actor || value.request_id !== pointer.requestId
      || value.revision !== pointer.expectedRevision + 1 || pointer.caseId !== null && value.case_id !== pointer.caseId
      || value.binding_id !== pointer.bindingId || value.binding_revision !== pointer.bindingRevision
      || value.device_id !== pointer.deviceId || value.policy_digest !== pointer.policyDigest || value.grant_digest !== pointer.grantDigest) throw invalid();
    if (!writePointer(owner, agent, null)) { set({ error: 'storage', storageReady: false }); return false; }
    set({ pending: null, lastSaved: value, error: null }); return true;
  };
  const settle = (value: unknown, pointer: ScriptCaseSavePointer, project: string, actor: string, agent: string, owner: string) => {
    if (!validCaseSaveState(value, project, actor, agent, pointer)) throw invalid();
    if (value.status === 'saved' && value.case) {
      if (accept(value.case, pointer, project, actor, agent, owner)) set({ saveState: value });
    } else if (value.status === 'preparing') set({ saveState: value });
    else if (!writePointer(owner, agent, null)) set({ error: 'storage', storageReady: false });
    else set({ pending: null, lastSaved: null, saveState: value, error: null });
  };
  return {
    ...initial,
    reset() { epoch++; listEpoch++; operation++; controller?.abort(); listController?.abort(); set(initial); },
    async select(agent) {
      get().reset(); const owner = identity();
      if (!owner || !scriptUuid(agent)) { set({ error: 'forbidden' }); return; }
      set({ identity: owner, agentId: agent });
      try {
        const raw = sessionStorage.getItem(key(owner, agent));
        const value: unknown = raw ? JSON.parse(raw) : { pending: null };
        if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).join() !== 'pending'
          || !('pending' in value) || value.pending !== null && !validCaseSavePointer(value.pending)) throw invalid();
        set({ pending: value.pending as ScriptCaseSavePointer | null, storageReady: true });
      } catch { set({ error: 'storage' }); return; }
      if (get().pending) await get().recover();
      await get().refresh();
    },
    async refresh(more = false) {
      const { identity: owner, agentId: agent, items, busy, loading, hasMore } = get(), user = useAuthStore.getState().user, stamp = epoch;
      if (!current(stamp, owner, agent) || !agent || !user || busy || loading || more && !hasMore) return;
      const skip = more ? items.length : 0, observation = ++listEpoch;
      listController?.abort(); listController = new AbortController(); set({ loading: true });
      try {
        const page = await AgentDeviceScriptCasesApi.list(agent, skip, listController.signal);
        if (!current(stamp, owner, agent) || observation !== listEpoch) return;
        if (page.project_id !== user.project_id || page.agent_id !== agent || page.skip !== skip || page.limit !== 20
          || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < skip + page.items.length
          || page.items.length > 20 || page.items.some(row => !validScriptCase(row, user.project_id, agent))
          || more && page.items.length === 0 && page.total > skip) throw invalid();
        const rows = more ? [...items, ...page.items] : page.items;
        if (new Set(rows.map(row => row.case_id)).size !== rows.length) throw invalid();
        set({ items: rows, hasMore: page.total > rows.length });
      } catch (error) { if (current(stamp, owner, agent) && observation === listEpoch) set({ error: kind(error) }); }
      finally { if (current(stamp, owner, agent) && observation === listEpoch) set({ loading: false }); }
    },
    async save(title, parameters, expected, editing = null) {
      const state = get(), { identity: owner, agentId: agent } = state, user = useAuthStore.getState().user, stamp = epoch;
      const device = useAgentDeviceScriptStore.getState(), binding = device.binding;
      if (!current(stamp, owner, agent) || !owner || !agent || !user || state.busy || state.pending || !state.storageReady
        || device.agentId !== agent || device.identity !== owner || device.busy || device.unknown || device.bindingPending
        || !binding?.enabled || binding.device_id !== device.deviceId || binding.policy_digest !== device.policy?.policy.digest
        || binding.grant_digest !== device.descriptor?.grant_digest) return false;
      if (!title.trim() || Array.from(title).length > 80 || /[\u0000-\u001f]/.test(title)
        || editing && (!validScriptCase(editing, user.project_id, agent) || editing.status === 'superseded')) {
        set({ error: 'configuration' }); return false;
      }
      const inputs = { ...parameters }, expectedFields = { ...expected };
      const submission = ++operation;
      set({ busy: true, error: null, lastSaved: null, saveState: null }); controller = new AbortController();
      let saved = false;
      try {
        const paused = await useAgentBusinessCurrentStateStore.getState().check(agent, true);
        if (!current(stamp, owner, agent) || operation !== submission) return false;
        if (!paused) { set({ error: 'active' }); return false; }
        const pointer: ScriptCaseSavePointer = { requestId: crypto.randomUUID(), caseId: editing?.case_id ?? null,
          expectedRevision: editing?.revision ?? 0, bindingId: binding.id, bindingRevision: binding.revision,
          deviceId: binding.device_id, policyDigest: binding.policy_digest, grantDigest: binding.grant_digest };
        if (!writePointer(owner, agent, pointer)) { set({ error: 'storage', storageReady: false }); return false; }
        set({ pending: pointer });
        const value = await AgentDeviceScriptCasesApi.save(agent, { request_id: pointer.requestId, case_id: pointer.caseId,
          expected_revision: pointer.expectedRevision, binding_id: pointer.bindingId, binding_revision: pointer.bindingRevision,
          title: title.trim(), parameters: inputs, expected_fields: expectedFields }, controller.signal);
        if (!current(stamp, owner, agent) || operation !== submission) return false;
        saved = accept(value, pointer, user.project_id, user.id, agent, owner);
      } catch (error) { if (current(stamp, owner, agent) && operation === submission) set({ error: kind(error) }); }
      finally { if (current(stamp, owner, agent) && operation === submission) set({ busy: false }); }
      if (saved) await get().refresh();
      return saved;
    },
    async recover() {
      const { identity: owner, agentId: agent, pending, busy } = get(), user = useAuthStore.getState().user, stamp = epoch;
      if (!current(stamp, owner, agent) || !owner || !agent || !user || !pending || busy) return;
      const observation = ++operation;
      controller?.abort(); controller = new AbortController(); set({ busy: true, error: null });
      try {
        const value = await AgentDeviceScriptCasesApi.saveState(agent, pending.requestId, controller.signal);
        if (!current(stamp, owner, agent) || observation !== operation) return;
        settle(value, pending, user.project_id, user.id, agent, owner);
      } catch (error) { if (current(stamp, owner, agent) && observation === operation) set({ error: kind(error) }); }
      finally { if (current(stamp, owner, agent) && observation === operation) set({ busy: false }); }
      if (current(stamp, owner, agent) && observation === operation && get().lastSaved) await get().refresh();
    },
    async cancel() {
      const { identity: owner, agentId: agent, pending, cancelling, storageReady } = get(), user = useAuthStore.getState().user, stamp = epoch;
      if (!current(stamp, owner, agent) || !owner || !agent || !pending || !user || cancelling || !storageReady) return;
      const cancellation = ++operation;
      controller?.abort(); controller = new AbortController(); set({ busy: true, cancelling: true, error: null });
      try {
        const value = await AgentDeviceScriptCasesApi.cancelSave(agent, {
          request_id: pending.requestId, binding_id: pending.bindingId, binding_revision: pending.bindingRevision,
          case_id: pending.caseId, expected_revision: pending.expectedRevision,
        }, controller.signal);
        if (!current(stamp, owner, agent) || operation !== cancellation) return;
        settle(value, pending, user.project_id, user.id, agent, owner);
      } catch (error) { if (current(stamp, owner, agent) && operation === cancellation) set({ error: kind(error) }); }
      finally { if (current(stamp, owner, agent) && operation === cancellation) set({ busy: false, cancelling: false }); }
      if (current(stamp, owner, agent) && operation === cancellation && get().lastSaved) await get().refresh();
    },
  };
});
useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id || state.user?.role !== previous.user?.role) {
    useAgentDeviceScriptCasesStore.getState().reset();
  }
});
