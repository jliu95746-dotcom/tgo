import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { AgentTrialReviewsApi } from '@/services/agentTrialReviewsApi';
import { APIError } from '@/services/api';
import type { KnowledgeCatalog, KnowledgeCatalogWrite } from '@/types/agentTrialReviews';

type CatalogError = 'network' | 'conflict' | 'forbidden' | 'invalid' | 'invalidResponse';
interface CatalogStore {
  identity: string | null;
  agentId: string | null;
  catalog: KnowledgeCatalog | null;
  questions: string[];
  editing: boolean;
  loading: boolean;
  saving: boolean;
  saved: boolean;
  pending: KnowledgeCatalogWrite | null;
  error: CatalogError | null;
  load: (agentId: string) => Promise<boolean>;
  open: () => void;
  close: () => void;
  change: (index: number, value: string) => void;
  save: () => Promise<boolean>;
}
const scope = () => {
  const user = useAuthStore.getState().user;
  return user?.role === 'admin' && user.project_id ? `${user.project_id}:${user.id}` : null;
};
const empty = { identity: null, agentId: null, catalog: null, questions: [], editing: false, loading: false,
  saving: false, saved: false, pending: null, error: null };
let generation = 0;
let controller: AbortController | null = null;
// Only unresolved commands survive employee navigation; login changes erase them.
const pendingSessions = new Map<string, { pending: KnowledgeCatalogWrite; catalog: KnowledgeCatalog }>();
function valid(value: KnowledgeCatalog, agent: string): boolean {
  return Boolean(value && value.agent_id === agent && Number.isInteger(value.revision) && value.revision >= 0 &&
    typeof value.current === 'boolean' && Array.isArray(value.cases) && value.cases.length === 6 &&
    value.cases.every((row, index) => row && typeof row.id === 'string' && row.id &&
      row.template_id === (index < 3 ? 'knowledge' : 'aftersales') && row.template_version === 1 &&
      row.criteria_version === 1 && row.expected_result === (index % 3 === 2 ? 'safe_refusal' : 'grounded_answer') &&
      typeof row.question === 'string' && row.question.trim() && row.question.length <= 4000) &&
    new Set(value.cases.map(row => row.id)).size === 6 &&
    (value.revision === 0 ? value.id === null : typeof value.id === 'string' && value.id));
}
function errorKind(error: unknown): CatalogError {
  if (error instanceof Error && error.message === 'invalidResponse') return 'invalidResponse';
  if (error instanceof APIError) {
    if ([401, 403, 404].includes(error.status)) return 'forbidden';
    if (error.status === 409) return 'conflict';
    if ([400, 422].includes(error.status)) return 'invalid';
  }
  return 'network';
}
function matchesRequest(value: KnowledgeCatalog, request: KnowledgeCatalogWrite): boolean {
  return value.request_key === request.request_key && value.configured_by === useAuthStore.getState().user?.id
    && value.revision === request.expected_revision + 1
    && value.cases.every((row, index) => row.question === request.questions[index]);
}
export const useAgentKnowledgeCatalogStore = create<CatalogStore>((set, get) => {
  const current = (version: number, agent: string) => generation === version && get().agentId === agent && get().identity === scope();
  let activeRead: { agent: string; identity: string | null; promise: Promise<boolean> } | null = null;
  return {
    ...empty,
    load: agent => {
      if (get().loading && activeRead?.agent === agent && activeRead.identity === scope()) return activeRead.promise;
      const promise = (async () => {
      if (!scope() || ((get().saving || get().loading) && get().agentId === agent)) return false;
      const previous = get();
      if (previous.identity !== scope()) pendingSessions.clear();
      else if (previous.agentId && previous.pending && previous.catalog) {
        pendingSessions.set(previous.agentId, { pending: previous.pending, catalog: previous.catalog });
      }
      const saved = pendingSessions.get(agent);
      controller?.abort(); controller = new AbortController(); const signal = controller.signal;
      const version = ++generation;
      set({ ...empty, identity: scope(), agentId: agent, loading: true, ...(saved ? {
        pending: saved.pending, catalog: saved.catalog, questions: saved.pending.questions, editing: true,
      } : {}) });
      try {
        const value = await AgentTrialReviewsApi.catalog(agent, signal);
        if (!current(version, agent) || signal.aborted) return false;
        if (!valid(value, agent) || !value.current) throw new Error('invalidResponse');
        if (saved && value.request_key === saved.pending.request_key && value.configured_by === useAuthStore.getState().user?.id) {
          if (!matchesRequest(value, saved.pending)) throw new Error('invalidResponse');
          pendingSessions.delete(agent);
          set({ catalog: value, questions: value.cases.map(row => row.question), pending: null, editing: false, saved: true });
          return true;
        } else {
          set({ catalog: value, questions: saved ? saved.pending.questions : value.cases.map(row => row.question),
            error: saved ? 'network' : null });
        }
      } catch (error) {
        if (current(version, agent) && !signal.aborted) set({ error: errorKind(error) });
      } finally { if (current(version, agent)) set({ loading: false }); }
      return false;
      })();
      const owned = { agent, identity: scope(), promise }; activeRead = owned;
      const clear = () => { if (activeRead === owned) activeRead = null; };
      void promise.then(clear, clear);
      return promise;
    },
    open: () => { if (get().catalog && !get().loading) set({ editing: true, saved: false }); },
    close: () => { if (!get().saving && !get().pending) set({ editing: false, error: null, questions: get().catalog?.cases.map(row => row.question) ?? [] }); },
    change: (index, value) => {
      if (get().saving || get().pending || !get().editing || index < 0 || index >= 6) return;
      set({ questions: get().questions.map((question, slot) => slot === index ? value : question), error: null, saved: false });
    },
    save: async () => {
      const { agentId: agent, catalog, questions, pending } = get();
      if (!scope() || !agent || !catalog || get().saving || get().loading || get().error === 'conflict') return false;
      const normalized = questions.map(question => question.trim());
      if (normalized.length !== 6 || normalized.some(question => !question || question.length > 4000) || new Set(normalized).size !== 6) {
        set({ error: 'invalid' }); return false;
      }
      const request = pending ?? { request_key: crypto.randomUUID(), expected_revision: catalog.revision, questions: normalized };
      const version = generation; controller = new AbortController(); const signal = controller.signal;
      pendingSessions.set(agent, { pending: request, catalog });
      set({ pending: request, saving: true, error: null });
      try {
        const value = await AgentTrialReviewsApi.saveCatalog(agent, request, signal);
        if (!current(version, agent) || signal.aborted) return false;
        if (!valid(value, agent) || !matchesRequest(value, request)) throw new Error('invalidResponse');
        pendingSessions.delete(agent);
        if (!value.current) { set({ pending: null, error: 'conflict' }); return false; }
        set({ catalog: value, questions: value.cases.map(row => row.question), pending: null, saved: true, editing: false });
        return true;
      } catch (error) {
        if (current(version, agent) && !signal.aborted) {
          const known = error instanceof APIError && [400, 401, 403, 404, 409, 422].includes(error.status);
          if (known) pendingSessions.delete(agent);
          set({ error: errorKind(error), ...(known ? { pending: null } : {}) });
        }
        return false;
      } finally { if (current(version, agent)) set({ saving: false }); }
    },
  };
});
useAuthStore.subscribe((next, previous) => {
  if (next.user?.id === previous.user?.id && next.user?.project_id === previous.user?.project_id && next.user?.role === previous.user?.role) return;
  generation += 1; controller?.abort(); pendingSessions.clear(); useAgentKnowledgeCatalogStore.setState(empty);
});
