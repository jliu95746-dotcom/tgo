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
  load: (agentId: string) => Promise<void>;
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
export const useAgentKnowledgeCatalogStore = create<CatalogStore>((set, get) => {
  const current = (version: number, agent: string) => generation === version && get().agentId === agent && get().identity === scope();
  return {
    ...empty,
    load: async agent => {
      if (!scope() || ((get().saving || get().loading) && get().agentId === agent)) return;
      controller?.abort(); controller = new AbortController(); const signal = controller.signal;
      const version = ++generation;
      set({ ...empty, identity: scope(), agentId: agent, loading: true });
      try {
        const value = await AgentTrialReviewsApi.catalog(agent, signal);
        if (!current(version, agent) || signal.aborted) return;
        if (!valid(value, agent) || !value.current) throw new Error('invalidResponse');
        set({ catalog: value, questions: value.cases.map(row => row.question) });
      } catch (error) {
        if (current(version, agent) && !signal.aborted) set({ error: errorKind(error) });
      } finally { if (current(version, agent)) set({ loading: false }); }
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
      set({ pending: request, saving: true, error: null });
      try {
        const value = await AgentTrialReviewsApi.saveCatalog(agent, request, signal);
        if (!current(version, agent) || signal.aborted) return false;
        if (!valid(value, agent) || value.request_key !== request.request_key || value.revision !== request.expected_revision + 1 ||
          value.cases.some((row, index) => row.question !== request.questions[index])) throw new Error('invalidResponse');
        if (!value.current) { set({ pending: null, error: 'conflict' }); return false; }
        set({ catalog: value, questions: value.cases.map(row => row.question), pending: null, saved: true, editing: false });
        return true;
      } catch (error) {
        if (current(version, agent) && !signal.aborted) {
          const known = error instanceof APIError && [400, 401, 403, 404, 409, 422].includes(error.status);
          set({ error: errorKind(error), ...(known ? { pending: null } : {}) });
        }
        return false;
      } finally { if (current(version, agent)) set({ saving: false }); }
    },
  };
});
useAuthStore.subscribe((next, previous) => {
  if (next.user?.id === previous.user?.id && next.user?.project_id === previous.user?.project_id && next.user?.role === previous.user?.role) return;
  generation += 1; controller?.abort(); useAgentKnowledgeCatalogStore.setState(empty);
});
