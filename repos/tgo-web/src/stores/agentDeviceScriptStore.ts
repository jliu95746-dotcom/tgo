import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAgentBusinessCurrentStateStore } from './agentBusinessCurrentStateStore';
import { useAgentActivationStore } from './agentActivationStore';
import { AgentDeviceScriptsApi } from '@/services/agentDeviceScriptsApi';
import { AgentDeviceScriptCasesApi } from '@/services/agentDeviceScriptCasesApi';
import { validScriptCase } from '@/utils/agentDeviceScriptCaseValidation';
import type { AgentScriptCaseRecord } from '@/types/agentDeviceScriptCases';
import { APIError } from '@/services/api';
import { listDevices, getDeviceScriptCatalog, getDeviceScriptPolicy, getDeviceScriptStatus } from '@/services/deviceControlApi';
import { scriptUuid, validScriptBinding, validScriptReport, validScriptRequestState } from '@/utils/agentDeviceScriptValidation';
import type { AgentScriptBinding, AgentScriptTrialReport, ScriptBindingPointer, ScriptTrialPointer, ScriptBusinessValues, ScriptTrialRequestState } from '@/types/agentDeviceScripts';
import type { Device } from '@/types/deviceControl';
import type { ScriptDescriptor, ScriptPolicyRead } from '@/types/deviceScriptPolicy';

type ErrorKind = 'forbidden' | 'network' | 'invalidResponse' | 'storage' | 'active' | 'configuration';
interface State {
  identity: string | null; agentId: string | null;
  binding: AgentScriptBinding | null; devices: Device[]; hasMore: boolean;
  deviceId: string; descriptor: ScriptDescriptor | null; policy: ScriptPolicyRead | null;
  bindingPending: ScriptBindingPointer | null; trialPointer: ScriptTrialPointer | null;
  report: AgentScriptTrialReport | null; unknown: boolean;
  requestState: ScriptTrialRequestState | null; checking: boolean; cancelling: boolean;
  loading: boolean; loadingDevice: boolean; loadingMore: boolean; busy: boolean; error: ErrorKind | null;
  select: (agent: string) => Promise<void>; reset: () => void; refresh: () => Promise<void>;
  loadMoreDevices: () => Promise<void>; chooseDevice: (device: string) => Promise<void>;
  saveBinding: (enabled: boolean) => Promise<void>;
  start: (parameters: ScriptBusinessValues, expected: ScriptBusinessValues, savedCase?: AgentScriptCaseRecord) => Promise<void>;
  startCase: (savedCase: AgentScriptCaseRecord) => Promise<void>;
  recover: () => Promise<void>; cancel: () => Promise<void>;
}
const empty = {
  identity: null, agentId: null, binding: null, devices: [], hasMore: false,
  deviceId: '', descriptor: null, policy: null, bindingPending: null, trialPointer: null,
  report: null, unknown: false, loading: false, loadingDevice: false, loadingMore: false, busy: false, error: null,
  requestState: null, checking: false, cancelling: false,
};
const scope = () => { const user = useAuthStore.getState().user; return user?.role === 'admin' ? `${user.project_id}:${user.id}` : null; };
const key = (identity: string, agent: string) => `tgo:employee-script:v1:${identity}:${agent}`;
const errorKind = (error: unknown): ErrorKind => error instanceof APIError && [401, 403].includes(error.status) ? 'forbidden'
  : error instanceof Error && error.message === 'invalidResponse' ? 'invalidResponse' : 'network';
const invalid = () => Error('invalidResponse');
const isDigest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
function validBindingPointer(value: unknown): value is ScriptBindingPointer {
  return record(value) && Object.keys(value).sort().join() === 'deviceId,enabled,expectedRevision,policyDigest,requestId'
    && scriptUuid(value.requestId) && typeof value.expectedRevision === 'number' && Number.isInteger(value.expectedRevision)
    && value.expectedRevision >= 0 && value.expectedRevision < 2147483647 && typeof value.enabled === 'boolean'
    && (value.enabled ? scriptUuid(value.deviceId) && isDigest(value.policyDigest) : value.deviceId === null && value.policyDigest === null);
}
function validTrialPointer(value: unknown): value is ScriptTrialPointer {
  return record(value) && Object.keys(value).sort().join() === 'bindingId,bindingRevision,deviceId,grantDigest,policyDigest,requestId'
    && [value.requestId, value.bindingId, value.deviceId].every(scriptUuid)
    && typeof value.bindingRevision === 'number' && Number.isInteger(value.bindingRevision) && value.bindingRevision >= 1
    && value.bindingRevision <= 2147483647 && isDigest(value.policyDigest) && isDigest(value.grantDigest);
}
function readPointers(identity: string, agent: string): { bindingPending: ScriptBindingPointer | null; trialPointer: ScriptTrialPointer | null } {
  const raw = sessionStorage.getItem(key(identity, agent));
  if (!raw) return { bindingPending: null, trialPointer: null };
  const value: unknown = JSON.parse(raw);
  if (!record(value) || Object.keys(value).sort().join() !== 'bindingPending,trialPointer') throw invalid();
  const { bindingPending: binding, trialPointer: trial } = value;
  if (binding !== null && !validBindingPointer(binding) || trial !== null && !validTrialPointer(trial)) throw invalid();
  return { bindingPending: binding, trialPointer: trial };
}
let generation = 0, selection = 0, requestObservation = 0;
let controller: AbortController | null = null, deviceController: AbortController | null = null;
let readController: AbortController | null = null, cancelController: AbortController | null = null;

export const useAgentDeviceScriptStore = create<State>((set, get) => {
  const current = (epoch: number, identity: string | null, agent: string | null) => Boolean(identity && agent
    && epoch === generation && identity === scope() && get().identity === identity && get().agentId === agent);
  const persist = () => {
    const { identity, agentId, bindingPending, trialPointer } = get();
    if (!identity || !agentId) return false;
    try {
      const value = JSON.stringify({ bindingPending, trialPointer });
      sessionStorage.setItem(key(identity, agentId), value);
      return sessionStorage.getItem(key(identity, agentId)) === value;
    } catch { return false; }
  };
  const acceptBinding = (value: AgentScriptBinding, pending: ScriptBindingPointer, project: string, actor: string, agent: string) => {
    if (!validScriptBinding(value, project, agent) || value.configured_by !== actor
      || value.revision !== pending.expectedRevision + 1 || value.enabled !== pending.enabled
      || pending.enabled && (value.device_id !== pending.deviceId || value.policy_digest !== pending.policyDigest)) throw invalid();
    set({ binding: value, bindingPending: null, report: null, error: null }); persist();
    useAgentActivationStore.getState().invalidate(agent);
  };
  const acceptReport = (value: AgentScriptTrialReport, pointer: ScriptTrialPointer, project: string, actor: string, agent: string) => {
    if (!validScriptReport(value, project, actor, agent, pointer)) throw invalid();
    const state = get().requestState;
    if (state?.request_id === pointer.requestId && knownTerminal(state)) return;
    set({ report: value, error: null });
  };
  const knownTerminal = (state: ScriptTrialRequestState) => state.status === 'failed' || state.status === 'cancelled'
    || state.status === 'ready' && ['confirmed', 'finished'].includes(state.stop_status);
  const acceptState = (value: ScriptTrialRequestState, pointer: ScriptTrialPointer, project: string, actor: string, agent: string) => {
    if (!validScriptRequestState(value, project, actor, agent, pointer)) throw invalid();
    set({ requestState: value, report: value.report, unknown: !knownTerminal(value), error: null });
  };
  const readRequest = async () => {
    const { identity, agentId: agent, trialPointer: pointer, checking, cancelling } = get(), user = useAuthStore.getState().user, epoch = generation;
    if (!current(epoch, identity, agent) || !agent || !user || !pointer || checking || cancelling) return;
    readController?.abort(); readController = new AbortController(); const observation = ++requestObservation;
    set({ checking: true, error: null });
    try {
      const value = await AgentDeviceScriptsApi.requestState(agent, pointer.requestId, readController.signal);
      if (!current(epoch, identity, agent) || observation !== requestObservation || get().trialPointer?.requestId !== pointer.requestId) return;
      acceptState(value, pointer, user.project_id, user.id, agent);
    } catch (error) { if (current(epoch, identity, agent) && observation === requestObservation) set({ error: errorKind(error) }); }
    finally { if (current(epoch, identity, agent) && observation === requestObservation) set({ checking: false }); }
  };
  return {
    ...empty,
    reset() { generation++; selection++; requestObservation++; controller?.abort(); deviceController?.abort(); readController?.abort(); cancelController?.abort(); set(empty); },
    async select(agent) {
      get().reset(); const identity = scope();
      if (!identity || !scriptUuid(agent)) { set({ error: 'forbidden' }); return; }
      set({ identity, agentId: agent });
      try { const restored = readPointers(identity, agent); set({ ...restored, unknown: Boolean(restored.trialPointer) }); }
      catch { set({ error: 'storage', unknown: true }); return; }
      await get().refresh();
      if (get().agentId === agent && (get().bindingPending || get().trialPointer)) await get().recover();
    },
    async refresh() {
      const { identity, agentId: agent, busy, loading } = get(), user = useAuthStore.getState().user, epoch = generation;
      if (!current(epoch, identity, agent) || !agent || !user || busy || loading) return;
      controller?.abort(); controller = new AbortController(); set({ loading: true, error: null });
      try {
        const [value, page] = await Promise.all([
          AgentDeviceScriptsApi.readBinding(agent, controller.signal), listDevices({ limit: 100 }, { signal: controller.signal }),
        ]);
        if (!current(epoch, identity, agent)) return;
        if (value.project_id !== user.project_id || value.agent_id !== agent
          || value.binding !== null && !validScriptBinding(value.binding, user.project_id, agent)
          || !Array.isArray(page.devices) || page.devices.some(row => row.project_id !== user.project_id || !scriptUuid(row.id))) throw invalid();
        set({ binding: value.binding, devices: page.devices, hasMore: page.total > page.devices.length });
        const device = get().deviceId || value.binding?.device_id;
        if (device) await get().chooseDevice(device);
      } catch (error) { if (current(epoch, identity, agent)) set({ error: errorKind(error), binding: null, descriptor: null, policy: null }); }
      finally { if (current(epoch, identity, agent)) set({ loading: false }); }
    },
    async loadMoreDevices() {
      const { identity, agentId: agent, loadingMore, hasMore, devices } = get(), user = useAuthStore.getState().user, epoch = generation;
      if (!current(epoch, identity, agent) || !user || !hasMore || loadingMore) return;
      set({ loadingMore: true });
      try {
        const page = await listDevices({ skip: devices.length, limit: 100 });
        if (!current(epoch, identity, agent)) return;
        if (!Array.isArray(page.devices) || page.devices.some(row => row.project_id !== user.project_id || !scriptUuid(row.id))
          || !page.devices.length && page.total > devices.length) throw invalid();
        const rows = [...devices, ...page.devices];
        if (new Set(rows.map(row => row.id)).size !== rows.length) throw invalid();
        set({ devices: rows, hasMore: page.total > rows.length });
      } catch (error) { if (current(epoch, identity, agent)) set({ error: errorKind(error) }); }
      finally { if (current(epoch, identity, agent)) set({ loadingMore: false }); }
    },
    async chooseDevice(deviceId) {
      const { identity, agentId: agent, busy } = get(), user = useAuthStore.getState().user, epoch = generation;
      if (!current(epoch, identity, agent) || !user || busy) return;
      deviceController?.abort(); deviceController = new AbortController(); const request = ++selection;
      set({ deviceId, descriptor: null, policy: null, loadingDevice: Boolean(deviceId) });
      if (!deviceId) return;
      try {
        const options = { signal: deviceController.signal };
        const [policy, catalog, local] = await Promise.all([
          getDeviceScriptPolicy(deviceId, options), getDeviceScriptCatalog(deviceId, options), getDeviceScriptStatus(deviceId, options),
        ]);
        if (!current(epoch, identity, agent) || request !== selection) return;
        const item = catalog.scripts?.find(row => row.script_id === policy.policy.script_id && row.grant_digest === policy.policy.grant_digest);
        if (policy.device_id !== deviceId || policy.project_id !== user.project_id || policy.status !== 'active' || policy.verified !== false
          || !isDigest(policy.policy.digest) || catalog.protocol_version !== 1 || !item || item.effect !== 'read_only'
          || local.device_id !== deviceId || local.project_id !== user.project_id || local.session.state !== 'active'
          || local.session.protocol_version !== 1 || !/^[0-9a-f]{32}$/.test(local.connection_instance_id ?? '')
          || !/^[0-9a-f]{32}$/.test(local.session.session_id ?? '')) throw invalid();
        set({ policy, descriptor: item, error: null });
      } catch (error) { if (current(epoch, identity, agent) && request === selection) set({ error: errorKind(error) }); }
      finally { if (current(epoch, identity, agent) && request === selection) set({ loadingDevice: false }); }
    },
    async saveBinding(enabled) {
      const state = get(), { identity, agentId: agent } = state, user = useAuthStore.getState().user, epoch = generation;
      if (!current(epoch, identity, agent) || !agent || !user || state.busy || state.checking || state.cancelling || state.loading || state.loadingDevice || state.bindingPending || state.unknown
        || enabled && (!state.policy || !state.descriptor) || !enabled && !state.binding?.enabled) return;
      set({ busy: true, error: null }); controller = new AbortController();
      try {
        const paused = await useAgentBusinessCurrentStateStore.getState().check(agent, true);
        if (!current(epoch, identity, agent)) return;
        if (!paused) { set({ error: 'active' }); return; }
        const pending: ScriptBindingPointer = { requestId: crypto.randomUUID(), expectedRevision: state.binding?.revision ?? 0,
          enabled, deviceId: enabled ? state.deviceId : null, policyDigest: enabled ? state.policy!.policy.digest : null };
        set({ bindingPending: pending });
        if (!persist()) { set({ bindingPending: null, error: 'storage' }); return; }
        const value = await AgentDeviceScriptsApi.saveBinding(agent, { request_id: pending.requestId, expected_revision: pending.expectedRevision, enabled,
          ...(enabled ? { device_id: pending.deviceId!, expected_policy_digest: pending.policyDigest! } : {}) }, controller.signal);
        if (!current(epoch, identity, agent)) return;
        acceptBinding(value, pending, user.project_id, user.id, agent);
      } catch (error) { if (current(epoch, identity, agent)) set({ error: errorKind(error) }); }
      finally { if (current(epoch, identity, agent)) set({ busy: false }); }
      if (current(epoch, identity, agent) && !get().bindingPending) await get().refresh();
    },
    async startCase(savedCase) { await get().start({}, {}, savedCase); },
    async start(parameters, expected, savedCase) {
      const state = get(), { identity, agentId: agent, binding, descriptor, policy } = state, user = useAuthStore.getState().user, epoch = generation;
      if (!current(epoch, identity, agent) || !agent || !user || !binding?.enabled || !descriptor || !policy
        || state.busy || state.checking || state.cancelling || state.loading || state.loadingDevice || state.bindingPending || state.unknown
        || binding.device_id !== state.deviceId || binding.script_id !== descriptor.script_id
        || binding.policy_digest !== policy.policy.digest || binding.grant_digest !== descriptor.grant_digest) return;
      if (savedCase && (!validScriptCase(savedCase, user.project_id, agent) || savedCase.status !== 'current'
        || savedCase.binding_id !== binding.id || savedCase.binding_revision !== binding.revision
        || savedCase.device_id !== binding.device_id || savedCase.policy_digest !== binding.policy_digest
        || savedCase.grant_digest !== binding.grant_digest || Date.parse(savedCase.expires_at) <= Date.now())) {
        set({ error: 'configuration' }); return;
      }
      const inputs = { ...parameters }, expectedFields = { ...expected };
      set({ busy: true, error: null }); controller = new AbortController();
      try {
        const paused = await useAgentBusinessCurrentStateStore.getState().check(agent, true);
        if (!current(epoch, identity, agent)) return;
        if (!paused) { set({ error: 'active' }); return; }
        const pointer: ScriptTrialPointer = { requestId: crypto.randomUUID(), bindingId: binding.id, bindingRevision: binding.revision,
          deviceId: binding.device_id, policyDigest: binding.policy_digest, grantDigest: binding.grant_digest };
        const oldPointer = state.trialPointer;
        set({ trialPointer: pointer, report: null, requestState: null, unknown: true });
        if (!persist()) { set({ trialPointer: oldPointer, unknown: Boolean(oldPointer), error: 'storage' }); return; }
        useAgentActivationStore.getState().invalidate(agent);
        const command = { request_id: pointer.requestId, binding_id: pointer.bindingId, binding_revision: pointer.bindingRevision };
        let value: AgentScriptTrialReport;
        if (savedCase) {
          const response = await AgentDeviceScriptCasesApi.run(agent, savedCase.id, command, controller.signal);
          if (!current(epoch, identity, agent)) return;
          if (!record(response) || Object.keys(response).sort().join() !== 'can_enable,case_version_id,report,scope'
            || response.scope !== 'employee_device_script_case_trial' || response.can_enable !== false
            || response.case_version_id !== savedCase.id) throw invalid();
          value = response.report;
        } else {
          value = await AgentDeviceScriptsApi.start(agent, { ...command, parameters: inputs, expected_fields: expectedFields }, controller.signal);
        }
        if (!current(epoch, identity, agent)) return;
        acceptReport(value, pointer, user.project_id, user.id, agent);
        await readRequest();
      } catch (error) { if (current(epoch, identity, agent) && !(get().requestState && knownTerminal(get().requestState!))) set({ error: errorKind(error) }); }
      finally { if (current(epoch, identity, agent)) set({ busy: false }); }
    },
    async recover() {
      const state = get(), { identity, agentId: agent, bindingPending, trialPointer } = state, user = useAuthStore.getState().user, epoch = generation;
      if (!bindingPending && trialPointer) { await readRequest(); return; }
      if (!current(epoch, identity, agent) || !agent || !user || state.busy || (!bindingPending && !trialPointer)) return;
      controller = new AbortController(); set({ busy: true, error: null });
      try {
        if (bindingPending) {
          const value = await AgentDeviceScriptsApi.recoverBinding(agent, bindingPending.requestId, controller.signal);
          if (!current(epoch, identity, agent)) return;
          acceptBinding(value, bindingPending, user.project_id, user.id, agent);
        }
      } catch (error) { if (current(epoch, identity, agent)) set({ error: errorKind(error) }); }
      finally { if (current(epoch, identity, agent)) set({ busy: false }); }
      if (bindingPending && current(epoch, identity, agent) && !get().bindingPending) await get().refresh();
    },
    async cancel() {
      const state = get(), { identity, agentId: agent, trialPointer: pointer } = state, user = useAuthStore.getState().user, epoch = generation;
      if (!current(epoch, identity, agent) || !agent || !user || !pointer || !state.unknown || state.cancelling || state.bindingPending) return;
      readController?.abort(); cancelController = new AbortController(); const observation = ++requestObservation;
      set({ cancelling: true, checking: false, error: null });
      try {
        const value = await AgentDeviceScriptsApi.cancelRequest(agent, { request_id: pointer.requestId, binding_id: pointer.bindingId, binding_revision: pointer.bindingRevision }, cancelController.signal);
        if (!current(epoch, identity, agent) || observation !== requestObservation || get().trialPointer?.requestId !== pointer.requestId) return;
        acceptState(value, pointer, user.project_id, user.id, agent);
      } catch (error) { if (current(epoch, identity, agent) && observation === requestObservation) set({ error: errorKind(error) }); }
      finally { if (current(epoch, identity, agent) && observation === requestObservation) set({ cancelling: false }); }
    },
  };
});
useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id || state.user?.role !== previous.user?.role) useAgentDeviceScriptStore.getState().reset();
});
