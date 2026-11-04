import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { AgentActivationPoliciesApi } from '@/services/agentActivationPoliciesApi';
import { BusinessScenariosApi } from '@/services/businessScenariosApi';
import { AgentActivationRequestsApi } from '@/services/agentActivationRequestsApi';
import { rememberActivation, rememberedActivation, forgetActivation, validActivationReceipt } from '@/utils/activationRequestRecovery';
import type { AgentActivationRequest, AgentActivationReceipt } from '@/types/agentActivationRequests';
import type { ScenarioTemplate } from '@/types/scenarioDrafts';
import type { AgentActivationPolicies, AgentActivationPolicyChange, AgentActivationPolicyRecord, AgentActivationSelection } from '@/types/agentActivationPolicies';
import type { AgentActivationReadiness } from '@/types/agentActivationReadiness';
import type { KnowledgeChannel } from '@/types/scenarioResources';
import { validBusinessCapabilities } from '@/utils/agentBusinessTrialValidation';

type Issue = 'unavailable' | 'invalid' | 'conflict' | 'forbidden' | 'unknown' | 'pending' | 'blocked';
type Pending = { kind: 'save'; command: AgentActivationPolicyChange }
  | { kind: 'remove'; channel: KnowledgeChannel; revision: number }
  | { kind: 'enable'; command: AgentActivationRequest };
export interface ActivationEntry {
  policies: AgentActivationPolicies | null;
  readiness: AgentActivationReadiness | null;
  policiesReady: boolean;
  readinessReady: boolean;
  loading: boolean;
  writing: boolean;
  enabling: boolean;
  error: Issue | null;
  pending: Pending | null;
  fetchedAt: number;
  receipt: AgentActivationReceipt | null;
}
interface Store {
  identity: string | null;
  entries: Record<string, ActivationEntry>;
  templates: ScenarioTemplate[];
  templatesReady: boolean;
  templatesLoading: boolean;
  templateFetchedAt: number;
  loadTemplates: (force?: boolean) => Promise<void>;
  load: (agentId: string, force?: boolean) => Promise<ActivationEntry | null>;
  save: (agentId: string, selection: AgentActivationSelection) => Promise<boolean>;
  remove: (agentId: string, channel: KnowledgeChannel) => Promise<boolean>;
  retry: (agentId: string) => Promise<boolean>;
  enable: (agentId: string, action: () => Promise<void>) => Promise<boolean>;
  recover: (agentId: string) => Promise<boolean>;
  retryActivation: (agentId: string, beforeSubmit: () => Promise<void>) => Promise<boolean>;
  invalidate: (agentId?: string) => void;
}
const empty = (): ActivationEntry => ({ policies: null, readiness: null, policiesReady: false,
  readinessReady: false, loading: false, writing: false, enabling: false, error: null, pending: null, fetchedAt: 0, receipt: null });
const channels = ['web', 'wecom_kf', 'app', 'phone', 'internal'];
const templates = ['knowledge', 'aftersales', 'logistics', 'assistant'];
const states = ['not_tested', 'in_progress', 'awaiting_review', 'needs_retest', 'failed', 'passed', 'unavailable'];
const hash = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const identity = () => {
  const user = useAuthStore.getState().user;
  return user?.role === 'admin' && user.project_id ? `${user.project_id}:${user.id}` : null;
};
let generation = 0;
const controllers = new Map<string, AbortController>();
const reads = new Map<string, Promise<ActivationEntry | null>>();
let templateRead: Promise<void> | null = null;
function validPolicy(row: AgentActivationPolicyRecord): boolean {
  return Boolean(row?.id && row.configured_by && templates.includes(row.template_id)
    && channels.includes(row.channel) && Number.isInteger(row.revision) && row.revision > 0
    && Number.isInteger(row.template_version) && row.template_version > 0
    && hash(row.requirements_revision) && typeof row.enabled === 'boolean');
}
function validPolicies(value: AgentActivationPolicies, agentId: string): boolean {
  const user = useAuthStore.getState().user;
  return Boolean(value && value.project_id === user?.project_id && value.agent_id === agentId
    && value.can_enable === false && Array.isArray(value.policies) && value.policies.length <= 5
    && value.policies.every(validPolicy) && new Set(value.policies.map(row => row.channel)).size === value.policies.length);
}
function validReadiness(value: AgentActivationReadiness, policies: AgentActivationPolicies | null, agentId: string): boolean {
  const user = useAuthStore.getState().user, now = Date.now();
  if (!value || value.project_id !== user?.project_id || value.agent_id !== agentId || value.checked_by !== user?.id
    || value.scope !== 'employee_activation' || typeof value.enabled !== 'boolean' || typeof value.can_enable !== 'boolean'
    || !Number.isFinite(Date.parse(value.checked_at)) || Date.parse(value.checked_at) < now - 60000
    || Date.parse(value.checked_at) > now + 30000 || !Array.isArray(value.channels) || value.channels.length > 5
    || !Array.isArray(value.blockers) || value.blockers.some(row => !row || typeof row.message !== 'string')) return false;
  if (value.business_evidence_revision !== undefined && value.business_evidence_revision !== null && !hash(value.business_evidence_revision)) return false;
  if (value.channels.some(row => !row || !templates.includes(row.template_id) || !channels.includes(row.channel)
    || !Number.isInteger(row.template_version) || row.template_version < 1 || !Number.isInteger(row.policy_revision)
    || row.policy_revision < 1 || !hash(row.requirements_revision) || !states.includes(row.status)
    || (row.valid_until !== null && (typeof row.valid_until !== 'string' || !Number.isFinite(Date.parse(row.valid_until)))))
    || new Set(value.channels.map(row => row.channel)).size !== value.channels.length) return false;
  if (value.channels.some(row => {
    const business = row.business_capabilities ?? [];
    if (!validBusinessCapabilities({ scope: 'employee_business_capabilities', can_enable: false, can_execute: false,
      project_id: value.project_id, checked_by: value.checked_by, agent_id: value.agent_id,
      checked_at: value.checked_at, capabilities: business }, value.project_id, value.checked_by, value.agent_id)) return true;
    return row.template_id === 'logistics' && row.status === 'passed' && (!hash(value.business_evidence_revision)
      || new Set(business.map(item => item.operation)).size !== 2
      || business.some(item => item.status !== 'passed'));
  })) return false;
  if (!value.can_enable) return true;
  const selected = policies?.policies.filter(row => row.enabled) ?? [];
  return value.blockers.length === 0 && hash(value.configuration_fingerprint) && hash(value.model_owner_revision)
    && selected.length > 0 && selected.length === value.channels.length && value.channels.every(row => {
      const configured = selected.find(policy => policy.channel === row.channel);
      return row.status === 'passed' && Boolean(row.valid_until && Date.parse(row.valid_until) > now)
        && configured?.template_id === row.template_id && configured.template_version === row.template_version
        && configured.revision === row.policy_revision && configured.requirements_revision === row.requirements_revision;
    });
}
function issue(error: unknown): Issue {
  const status = typeof error === 'object' && error && 'status' in error ? error.status : undefined;
  if (status === 401 || status === 403) return 'forbidden';
  if (status === 409) return 'conflict';
  if (status === 400 || status === 404 || status === 422) return 'invalid';
  return 'unknown';
}
export function activationUsable(entry: ActivationEntry | undefined, now = Date.now()): boolean {
  return Boolean(entry?.readinessReady && !entry.error && !entry.loading && !entry.writing && !entry.enabling && !entry.pending
    && entry.readiness?.can_enable && Date.parse(entry.readiness.checked_at) > now - 60000
    && entry.readiness.channels.every(row => row.valid_until && Date.parse(row.valid_until) > now));
}
export const useAgentActivationStore = create<Store>((set, get) => {
  const put = (key: string, value: ActivationEntry) => set(state => ({ entries: { ...state.entries, [key]: value } }));
  const owns = (scope: string, version: number, controller?: AbortController) => identity() === scope
    && generation === version && !controller?.signal.aborted;
  const request = async (agentId: string, submit: boolean, beforeSubmit?: () => Promise<void>): Promise<boolean> => {
    const scope = identity(), current = get().entries[agentId];
    if (!scope || !current || current.enabling || current.writing || current.pending?.kind !== 'enable') return false;
    const command = current.pending.command, version = generation, controller = new AbortController();
    const key = `activation:${agentId}`;
    controllers.set(key, controller);
    put(agentId, { ...current, enabling: true, readinessReady: false, error: null });
    try {
      if (submit) await beforeSubmit?.();
      if (!owns(scope, version, controller)) return false;
      const receipt = submit ? await AgentActivationRequestsApi.submit(agentId, command, controller.signal)
        : await AgentActivationRequestsApi.lookup(agentId, command.request_key, controller.signal);
      if (!owns(scope, version, controller)) return false;
      const user = useAuthStore.getState().user!;
      if (!validActivationReceipt(receipt, command, user.project_id, user.id, agentId)) throw new Error('invalidActivationReceipt');
      const previous = get().entries[agentId];
      if (receipt.status === 'not_found') {
        put(agentId, { ...previous, enabling: false, receipt, error: 'unknown' });
        return false;
      }
      forgetActivation(scope, agentId);
      get().invalidate();
      put(agentId, { ...get().entries[agentId], enabling: false, pending: null, receipt,
        error: receipt.status === 'rejected' ? 'blocked' : null });
      return receipt.status === 'completed';
    } catch {
      if (owns(scope, version, controller)) put(agentId, { ...get().entries[agentId], enabling: false, error: 'unknown', readinessReady: false });
      return false;
    } finally { if (controllers.get(key) === controller) controllers.delete(key); }
  };
  const write = async (agentId: string): Promise<boolean> => {
    const scope = identity(), current = get().entries[agentId];
    if (!scope || get().identity !== scope || !current?.pending || current.writing || current.enabling
      || current.pending.kind === 'enable') return false;
    const pending = current.pending, version = generation, controller = new AbortController();
    const key = `write:${agentId}`; controllers.set(key, controller);
    put(agentId, { ...current, writing: true, readinessReady: false, error: null });
    try {
      const result = pending.kind === 'save'
        ? await AgentActivationPoliciesApi.save(agentId, pending.command, controller.signal)
        : await AgentActivationPoliciesApi.remove(agentId, pending.channel, pending.revision, controller.signal);
      if (!owns(scope, version, controller)) return false;
      const revision = pending.kind === 'save' ? pending.command.expected_revision : pending.revision;
      if (!validPolicy(result) || result.configured_by !== useAuthStore.getState().user?.id
        || result.revision < revision || result.revision > revision + 1
        || (pending.kind === 'save' ? result.template_id !== pending.command.selection.template_id
          || result.template_version !== pending.command.selection.template_version
          || result.channel !== pending.command.selection.channel || !result.enabled
          : result.channel !== pending.channel || result.enabled))
        throw new Error('invalidResponse');
      const previous = get().entries[agentId];
      const rows = [...(previous.policies?.policies ?? []).filter(row => row.channel !== result.channel), result];
      put(agentId, { ...previous, policies: previous.policies ? { ...previous.policies, policies: rows } : null,
        writing: false, pending: null, error: null, readinessReady: false });
      return true;
    } catch (error) {
      if (owns(scope, version, controller)) {
        const problem = issue(error), previous = get().entries[agentId];
        put(agentId, { ...previous, writing: false, error: problem, readinessReady: false,
          policiesReady: problem !== 'conflict' && problem !== 'forbidden' && previous.policiesReady,
          pending: problem === 'unknown' ? pending : null });
      }
      return false;
    } finally { if (controllers.get(key) === controller) controllers.delete(key); }
  };
  return {
    identity: null, entries: {}, templates: [], templatesReady: false, templatesLoading: false, templateFetchedAt: 0,
    loadTemplates: async (force = false) => {
      const scope = identity(); if (!scope) return;
      if (get().identity !== scope) set({ identity: scope, entries: {}, templates: [], templatesReady: false });
      if (templateRead) return templateRead;
      if (!force && get().templatesReady && Date.now() - get().templateFetchedAt < 15000) return;
      const version = generation, controller = new AbortController();
      controllers.set('templates', controller);
      set({ templatesLoading: true, templatesReady: false });
      const promise = (async () => {
        try {
          const rows = await BusinessScenariosApi.templates('agents', controller.signal);
          if (!owns(scope, version, controller)) return;
          if (!Array.isArray(rows) || rows.some(row => !row || row.module !== 'agents' || !templates.includes(row.id)
            || !Number.isInteger(row.version) || row.version < 1 || typeof row.available !== 'boolean' || !row.title)
            || new Set(rows.map(row => row.id)).size !== rows.length) throw new Error('invalidTemplates');
          set({ templates: rows, templatesReady: true, templateFetchedAt: Date.now() });
        } catch { if (owns(scope, version, controller)) set({ templates: [], templatesReady: false }); }
        finally { if (owns(scope, version, controller)) set({ templatesLoading: false }); }
      })();
      templateRead = promise;
      try { await promise; }
      finally { if (templateRead === promise) templateRead = null; if (controllers.get('templates') === controller) controllers.delete('templates'); }
    },
    load: async (agentId, force = false) => {
      const scope = identity(); if (!scope) return null;
      if (get().identity !== scope) set({ identity: scope, entries: {} });
      const existing = reads.get(agentId); if (existing) return existing;
      let previous = get().entries[agentId];
      if (!previous?.pending) {
        try {
          const command = rememberedActivation(scope, agentId);
          if (command) previous = { ...(previous ?? empty()), pending: { kind: 'enable', command }, error: 'unknown' };
        } catch {
          put(agentId, { ...(previous ?? empty()), error: 'unavailable', readinessReady: false });
          return get().entries[agentId];
        }
      }
      if (previous?.writing) return previous;
      if (!force && previous?.policiesReady && previous.readinessReady && Date.now() - previous.fetchedAt < 15000) return previous;
      const version = generation, controller = new AbortController(), key = `read:${agentId}`;
      controllers.set(key, controller);
      put(agentId, { ...(previous ?? empty()), loading: true, readinessReady: false, error: null });
      const promise = (async (): Promise<ActivationEntry | null> => {
        const [policyResult, checkResult] = await Promise.allSettled([
          AgentActivationPoliciesApi.read(agentId, controller.signal),
          AgentActivationPoliciesApi.readiness(agentId, controller.signal),
        ]);
        if (!owns(scope, version, controller)) return null;
        const policiesReady = policyResult.status === 'fulfilled' && validPolicies(policyResult.value, agentId);
        const policies = policiesReady && policyResult.status === 'fulfilled' ? policyResult.value : previous?.policies ?? null;
        const readinessReady = checkResult.status === 'fulfilled' && validReadiness(checkResult.value, policiesReady ? policies : null, agentId);
        const value: ActivationEntry = { ...get().entries[agentId], policies,
          readiness: readinessReady && checkResult.status === 'fulfilled' ? checkResult.value : previous?.readiness ?? null,
          policiesReady, readinessReady, loading: false, fetchedAt: Date.now(),
          error: policiesReady && readinessReady ? null : 'unavailable' };
        put(agentId, value); return value;
      })();
      reads.set(agentId, promise);
      try { return await promise; }
      finally { if (reads.get(agentId) === promise) reads.delete(agentId); if (controllers.get(key) === controller) controllers.delete(key); }
    },
    save: async (agentId, selection) => {
      const current = get().entries[agentId];
      if (get().identity !== identity() || !current?.policiesReady || current.loading || current.writing || current.enabling
        || current.pending || current.readiness?.enabled) return false;
      const previous = current.policies?.policies.find(row => row.channel === selection.channel);
      put(agentId, { ...current, pending: { kind: 'save', command: { selection: { ...selection }, expected_revision: previous?.revision ?? 0 } } });
      return write(agentId);
    },
    remove: async (agentId, channel) => {
      const current = get().entries[agentId], previous = current?.policies?.policies.find(row => row.channel === channel && row.enabled);
      if (get().identity !== identity() || !current?.policiesReady || !previous || current.pending
        || current.loading || current.writing || current.enabling || current.readiness?.enabled) return false;
      put(agentId, { ...current, pending: { kind: 'remove', channel, revision: previous.revision } });
      return write(agentId);
    },
    retry: write,
    enable: async (agentId, action) => {
      const scope = identity(), version = generation;
      if (!scope || Object.values(get().entries).some(row => row.enabling || row.writing)
        || get().entries[agentId]?.pending) return false;
      put(agentId, { ...(get().entries[agentId] ?? empty()), enabling: true, error: null });
      const value = await get().load(agentId, true);
      if (!owns(scope, version)) return false;
      if (!value || !activationUsable({ ...value, enabling: false })) {
        put(agentId, { ...(value ?? get().entries[agentId]), enabling: false, error: 'blocked' });
        return false;
      }
      try {
        await action();
        if (!owns(scope, version)) return false;
        const proof = value.readiness!;
        const command: AgentActivationRequest = { request_key: crypto.randomUUID(), expected: {
          checked_at: proof.checked_at, configuration_fingerprint: proof.configuration_fingerprint!, model_owner_revision: proof.model_owner_revision!,
          business_evidence_revision: proof.business_evidence_revision ?? null,
          channels: proof.channels.map(({ template_id, template_version, channel, policy_revision, requirements_revision }) =>
            ({ template_id, template_version, channel, policy_revision, requirements_revision })),
        } };
        rememberActivation(scope, agentId, command);
        put(agentId, { ...get().entries[agentId], enabling: false, pending: { kind: 'enable', command }, receipt: null });
        return await request(agentId, true);
      } catch (error) {
        if (owns(scope, version)) {
          const problem = issue(error);
          put(agentId, { ...get().entries[agentId], enabling: false, readinessReady: false,
            error: problem === 'unknown' ? 'unavailable' : problem });
        }
        return false;
      }
    },
    recover: agentId => request(agentId, false),
    retryActivation: async (agentId, beforeSubmit) => {
      const current = get().entries[agentId];
      if (current?.receipt?.status !== 'not_found' || current.pending?.kind !== 'enable') return false;
      return request(agentId, true, beforeSubmit);
    },
    invalidate: agentId => set(state => ({ entries: Object.fromEntries(Object.entries(state.entries).map(([key, entry]) =>
      [key, !agentId || key === agentId ? { ...entry, readinessReady: false, fetchedAt: 0 } : entry])) })),
  };
});
useAuthStore.subscribe((next, previous) => {
  if (next.user?.id === previous.user?.id && next.user?.project_id === previous.user?.project_id && next.user?.role === previous.user?.role) return;
  generation += 1; controllers.forEach(controller => controller.abort()); controllers.clear(); reads.clear(); templateRead = null;
  useAgentActivationStore.setState({ identity: null, entries: {}, templates: [], templatesReady: false, templatesLoading: false, templateFetchedAt: 0 });
});
