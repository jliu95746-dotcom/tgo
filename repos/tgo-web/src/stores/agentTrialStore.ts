import { create } from 'zustand';
import { AgentTrialsApi } from '@/services/agentTrialsApi';
import { APIError } from '@/services/api';
import type { AgentTrialReport, AgentTrialRequest } from '@/types/agentTrials';
import type { KnowledgeChannel } from '@/types/scenarioResources';
import { useAuthStore } from './authStore';

type TrialError = 'network' | 'forbidden' | 'missing' | 'conflict' | 'quota' | 'invalid' | 'unavailable' | 'invalidReport';
interface PendingTrial { requestKey: string; request: AgentTrialRequest | null }
interface TrialSession { pending: PendingTrial | null; report: AgentTrialReport | null; unknown: boolean }
interface TrialState extends TrialSession {
  identity: string | null;
  agentId: string | null;
  reports: AgentTrialReport[];
  sessions: Record<string, TrialSession>;
  loading: boolean;
  historyReady: boolean;
  submitting: boolean;
  cancelling: boolean;
  recovering: boolean;
  error: TrialError | null;
  select: (agentId: string) => Promise<void>;
  refresh: () => Promise<void>;
  start: (message: string, channel?: KnowledgeChannel) => Promise<void>;
  retry: () => Promise<void>;
  recover: () => Promise<void>;
  cancel: () => Promise<void>;
  inspect: (reportId: string) => void;
  leave: (agentId: string) => void;
}

const emptySession: TrialSession = { pending: null, report: null, unknown: false };
let scopeVersion = 0;
let generation: AbortController | null = null;
let history: AbortController | null = null;
let control: AbortController | null = null;
const identity = () => {
  const user = useAuthStore.getState().user;
  return user?.role === 'admin' && user.project_id ? `${user.project_id}:${user.id}` : null;
};
function abortRequests() {
  scopeVersion += 1;
  generation?.abort(); history?.abort(); control?.abort();
  generation = null; history = null; control = null;
}
function errorKind(error: unknown): TrialError {
  if (error instanceof Error && error.message === 'invalidReport') return 'invalidReport';
  if (!(error instanceof APIError)) return 'network';
  if ([401, 403].includes(error.status)) return 'forbidden';
  if (error.status === 404) return 'missing';
  if (error.status === 409) return 'conflict';
  if ([402, 429].includes(error.status)) return 'quota';
  if ([400, 422].includes(error.status)) return 'invalid';
  if (error.status === 503) return 'unavailable';
  return 'network';
}
const terminal = (report: AgentTrialReport) => report.status !== 'running';
function mergeReport(previous: AgentTrialReport | undefined, incoming: AgentTrialReport): AgentTrialReport {
  if (!previous || !terminal(previous)) return incoming;
  if (!terminal(incoming)) return previous;
  const oldCheck = previous.applicability?.checked_at;
  const newCheck = incoming.applicability?.checked_at;
  if (oldCheck && newCheck && new Date(newCheck).getTime() < new Date(oldCheck).getTime()) return previous;
  // Preserve the immutable terminal artifact, but refresh owner-read applicability.
  return { ...previous, applicability: incoming.applicability ?? {
    scope: 'agent_configuration_and_knowledge', status: 'not_recorded', checked_at: null,
    issues: ['snapshot_not_recorded'],
  } };
}

/** Questions stay in memory; identity changes erase every employee's trial session. */
export const useAgentTrialStore = create<TrialState>((set, get) => {
  const update = (patch: Partial<TrialState>) => {
    set(patch);
    const state = get();
    if (state.agentId) set({ sessions: { ...state.sessions, [state.agentId]: {
      pending: state.pending, report: state.report, unknown: state.unknown,
    } } });
  };
  const current = (version: number, agentId: string) => version === scopeVersion &&
    get().agentId === agentId && get().identity === identity();
  const validate = (report: AgentTrialReport, agentId: string, requestKey?: string) => {
    if (report.agent_id !== agentId || (requestKey && report.request_key !== requestKey) ||
      report.can_enable !== false || report.assessment !== 'not_reviewed' ||
      !['running', 'completed', 'blocked', 'failed', 'cancelled'].includes(report.status)) {
      throw new Error('invalidReport');
    }
  };
  const accept = (incoming: AgentTrialReport, agentId: string, requestKey?: string) => {
    validate(incoming, agentId, requestKey);
    const state = get();
    const previous = state.reports.find(value => value.id === incoming.id);
    // A late response cannot revive a terminal report, including cancellation.
    const report = mergeReport(previous, incoming);
    const reports = [report, ...state.reports.filter(value => value.id !== report.id)]
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
    const resolved = state.pending?.requestKey === report.request_key && terminal(report);
    update({ report, reports, ...(resolved ? { pending: null, unknown: false } : {}) });
  };
  const submit = async (request: AgentTrialRequest) => {
    const { agentId } = get();
    if (!agentId || !identity() || get().submitting || get().cancelling) return;
    const version = scopeVersion;
    const controller = new AbortController(); generation = controller;
    update({ submitting: true, error: null, unknown: false });
    try {
      const report = await AgentTrialsApi.start(agentId, request, controller.signal);
      if (current(version, agentId) && !controller.signal.aborted) accept(report, agentId, request.request_key);
    } catch (error) {
      if (!current(version, agentId) || controller.signal.aborted) return;
      const rejected = error instanceof APIError && [400, 401, 403, 404, 422].includes(error.status);
      update({ error: errorKind(error), unknown: !rejected, ...(rejected ? { pending: null } : {}) });
    } finally {
      if (current(version, agentId) && generation === controller) update({ submitting: false });
      if (generation === controller) generation = null;
    }
  };
  return {
    ...emptySession, identity: null, agentId: null, reports: [], sessions: {},
    loading: false, historyReady: false, submitting: false, cancelling: false, recovering: false, error: null,
    select: async agentId => {
      abortRequests();
      const scope = identity();
      if (!scope) { set({ agentId: null }); return; }
      const saved = get().sessions[agentId] ?? emptySession;
      const version = scopeVersion;
      set({ ...saved, identity: scope, agentId, reports: saved.report ? [saved.report] : [],
        loading: false, historyReady: false, submitting: false, cancelling: false, recovering: false, error: null });
      await get().refresh();
      const pending = get().pending;
      if (current(version, agentId) && pending && !get().submitting &&
        !get().reports.some(report => report.request_key === pending.requestKey)) await get().recover();
    },
    refresh: async () => {
      const { agentId } = get();
      if (!agentId || !identity() || get().loading) return;
      const version = scopeVersion;
      const controller = new AbortController(); history = controller;
      update({ loading: true, historyReady: false, error: null });
      try {
        const reports = await AgentTrialsApi.list(agentId, controller.signal);
        if (!current(version, agentId)) return;
        reports.forEach(report => validate(report, agentId));
        const existing = new Map(get().reports.map(value => [value.id, value]));
        const merged = reports.map(value => {
          const previous = existing.get(value.id);
          return mergeReport(previous, value);
        });
        const pending = get().pending;
        const chosen = pending ? merged.find(value => value.request_key === pending.requestKey)
          : merged.find(value => value.status === 'running') ?? merged[0];
        update({ reports: merged, historyReady: true });
        if (chosen) {
          if (chosen.status === 'running' && !pending) update({ pending: { requestKey: chosen.request_key, request: null } });
          accept(chosen, agentId, pending?.requestKey);
        }
      } catch (error) {
        if (current(version, agentId)) update({ error: errorKind(error) });
      } finally {
        if (current(version, agentId)) update({ loading: false });
        if (history === controller) history = null;
      }
    },
    start: async (message, channel = 'web') => {
      const state = get();
      if (!state.agentId || !identity() || !state.historyReady || state.pending || state.loading || state.recovering || state.cancelling) return;
      const trimmed = message.trim();
      if (!trimmed || trimmed.length > 4000) { update({ error: 'invalid' }); return; }
      const request: AgentTrialRequest = { request_key: crypto.randomUUID(), message: trimmed, channel, timeout: 45 };
      update({ pending: { requestKey: request.request_key, request }, report: null });
      await submit(request);
    },
    retry: async () => {
      const state = get();
      if (state.unknown && state.pending?.request && !state.recovering && !state.loading) await submit(state.pending.request);
    },
    recover: async () => {
      const { agentId, pending } = get();
      if (!agentId || !pending || !identity() || get().recovering || get().cancelling) return;
      const version = scopeVersion;
      const controller = new AbortController(); control = controller;
      update({ recovering: true, error: null });
      try {
        const report = await AgentTrialsApi.byRequest(agentId, pending.requestKey, controller.signal);
        if (current(version, agentId)) accept(report, agentId, pending.requestKey);
      } catch (error) {
        if (current(version, agentId) && get().pending?.requestKey === pending.requestKey) update({ unknown: true, error: errorKind(error) });
      } finally {
        if (current(version, agentId)) update({ recovering: false });
        if (control === controller) control = null;
      }
    },
    cancel: async () => {
      const { agentId, pending, report } = get();
      if (!agentId || !pending || !identity() || get().cancelling || get().recovering) return;
      generation?.abort();
      const version = scopeVersion;
      const controller = new AbortController(); control = controller;
      update({ cancelling: true, submitting: false, error: null });
      try {
        const original = report?.request_key === pending.requestKey ? report
          : await AgentTrialsApi.byRequest(agentId, pending.requestKey, controller.signal);
        if (!current(version, agentId)) return;
        validate(original, agentId, pending.requestKey);
        const result = original.status === 'running'
          ? await AgentTrialsApi.cancel(agentId, original.id, controller.signal) : original;
        if (result.id !== original.id) throw new Error('invalidReport');
        if (current(version, agentId)) accept(result, agentId, pending.requestKey);
      } catch (error) {
        if (current(version, agentId)) update({ unknown: true, error: errorKind(error) });
      } finally {
        if (current(version, agentId)) update({ cancelling: false });
        if (control === controller) control = null;
      }
    },
    inspect: reportId => {
      if (get().pending || get().submitting) return;
      const report = get().reports.find(value => value.id === reportId);
      if (report) update({ report });
    },
    leave: agentId => {
      if (get().agentId !== agentId) return;
      update({ unknown: Boolean(get().pending), submitting: false, recovering: false, cancelling: false, loading: false });
      abortRequests();
      set({ agentId: null });
    },
  };
});

useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id ||
    state.user?.role !== previous.user?.role) {
    abortRequests();
    useAgentTrialStore.setState({ ...emptySession, identity: null, agentId: null, sessions: {}, reports: [],
      loading: false, historyReady: false, submitting: false, cancelling: false, recovering: false, error: null });
  }
});
