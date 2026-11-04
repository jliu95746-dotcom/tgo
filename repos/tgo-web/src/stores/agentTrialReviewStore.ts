import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { APIError } from '@/services/api';
import { AgentTrialReviewsApi } from '@/services/agentTrialReviewsApi';
import type { KnowledgeReviewCase, KnowledgeReviewChecks, KnowledgeReviewRequest, KnowledgeReviewResponse, KnowledgeReviewState } from '@/types/agentTrialReviews';

type ReviewError = 'network' | 'forbidden' | 'missing' | 'conflict' | 'invalid' | 'unavailable' | 'invalidResponse';
interface ReviewSession { pending: KnowledgeReviewRequest | null; unknown: boolean }
interface ReviewStore extends ReviewSession {
  identity: string | null;
  agentId: string | null;
  reportId: string | null;
  question: string;
  catalog: KnowledgeReviewCase[];
  state: KnowledgeReviewState | null;
  sessions: Record<string, ReviewSession>;
  ready: boolean;
  loading: boolean;
  submitting: boolean;
  error: ReviewError | null;
  select: (agentId: string, reportId: string | null, question: string) => Promise<void>;
  refresh: () => Promise<void>;
  submit: (caseId: string, decision: 'passed' | 'rejected', checks: KnowledgeReviewChecks, note: string) => Promise<void>;
  retry: () => Promise<void>;
  leave: (agentId: string) => void;
}
const empty: ReviewSession = { pending: null, unknown: false };
let generation = 0;
let read: AbortController | null = null;
let write: AbortController | null = null;
const identity = () => {
  const user = useAuthStore.getState().user;
  return user?.role === 'admin' && user.project_id ? `${user.project_id}:${user.id}` : null;
};
const sessionKey = (agent: string, report: string | null) => `${agent}:${report ?? ''}`;
function abort() { generation += 1; read?.abort(); write?.abort(); read = null; write = null; }
function errorKind(error: unknown): ReviewError {
  if (error instanceof Error && error.message === 'invalidResponse') return 'invalidResponse';
  if (!(error instanceof APIError)) return 'network';
  if ([401, 403].includes(error.status)) return 'forbidden';
  if (error.status === 404) return 'missing';
  if (error.status === 409) return 'conflict';
  if ([400, 422].includes(error.status)) return 'invalid';
  if (error.status === 503) return 'unavailable';
  return 'network';
}
function validCase(value: KnowledgeReviewCase): boolean {
  return Boolean(value && typeof value.id === 'string' && value.id && typeof value.question === 'string' && value.question &&
    ['knowledge', 'aftersales'].includes(value.template_id) && Number.isInteger(value.template_version) && value.template_version >= 1 &&
    value.criteria_version === 1 && ['grounded_answer', 'safe_refusal'].includes(value.expected_result));
}
function sameCase(left: KnowledgeReviewCase, right: KnowledgeReviewCase): boolean {
  return left.id === right.id && left.template_id === right.template_id && left.template_version === right.template_version &&
    left.criteria_version === right.criteria_version && left.question === right.question && left.expected_result === right.expected_result &&
    (left.valid_from ?? null) === (right.valid_from ?? null);
}
function validReview(value: KnowledgeReviewResponse, agent: string, report: string, actor: string): boolean {
  return Boolean(value && value.agent_id === agent && value.report_id === report && value.reviewed_by === actor &&
    typeof value.id === 'string' && value.id && typeof value.request_key === 'string' && value.request_key &&
    value.scope === 'knowledge_case' && value.can_enable === false && Number.isInteger(value.revision) && value.revision >= 1 &&
    ['passed', 'rejected'].includes(value.decision) && validCase(value.case) && value.checks &&
    ['answer_correct', 'evidence_checked', 'no_unverified_claims', 'expected_behavior'].every(key =>
      typeof value.checks[key as keyof KnowledgeReviewChecks] === 'boolean') &&
    typeof value.effective === 'boolean' && typeof value.superseded === 'boolean' &&
    ['current', 'changed'].includes(value.case_status) && Number.isFinite(Date.parse(value.created_at)) && Number.isFinite(Date.parse(value.valid_until)));
}
function matchesRequest(value: KnowledgeReviewResponse, request: KnowledgeReviewRequest): boolean {
  return value.request_key === request.request_key && value.case.id === request.case_id && value.decision === request.decision &&
    Object.keys(request.checks).every(key => value.checks[key as keyof KnowledgeReviewChecks] === request.checks[key as keyof KnowledgeReviewChecks]);
}

/** Review drafts stay in memory; login/role changes erase them and abort old requests. */
export const useAgentTrialReviewStore = create<ReviewStore>((set, get) => {
  const update = (patch: Partial<ReviewStore>) => {
    set(patch);
    const current = get();
    if (current.agentId) set({ sessions: { ...current.sessions, [sessionKey(current.agentId, current.reportId)]: {
      pending: current.pending, unknown: current.unknown,
    } } });
  };
  const current = (version: number, agent: string, report: string | null) =>
    generation === version && get().agentId === agent && get().reportId === report && get().identity === identity();
  const acceptCatalog = (catalog: KnowledgeReviewCase[]) => {
    if (!Array.isArray(catalog) || catalog.some(row => !validCase(row)) || new Set(catalog.map(row => row.id)).size !== catalog.length) throw new Error('invalidResponse');
    set({ catalog });
  };
  const validateState = (value: KnowledgeReviewState, agent: string, report: string) => {
    const actor = useAuthStore.getState().user?.id;
    if (!value || value.agent_id !== agent || value.report_id !== report || value.scope !== 'knowledge_case' || value.can_enable !== false ||
      !Array.isArray(value.available_cases) || !Array.isArray(value.reviews) || typeof value.can_review !== 'boolean' ||
      !Number.isFinite(Date.parse(value.valid_until)) || !value.applicability || value.applicability.scope !== 'agent_configuration_and_knowledge' ||
      !['not_recorded', 'not_checked', 'current', 'changed', 'unknown'].includes(value.applicability.status) ||
      value.available_cases.some(row => !validCase(row) || row.question.trim() !== get().question.trim() ||
        !get().catalog.some(spec => sameCase(row, spec))) ||
      value.reviews.some(row => !actor || !validReview(row, agent, report, actor))) throw new Error('invalidResponse');
  };
  const accept = (value: KnowledgeReviewState) => {
    const pending = get().pending;
    const found = pending && value.reviews.find(row => row.request_key === pending.request_key);
    if (found && pending && !matchesRequest(found, pending)) throw new Error('invalidResponse');
    update({ state: value, ready: true, error: null, ...(found ? { pending: null, unknown: false } : {}) });
  };
  const send = async (request: KnowledgeReviewRequest) => {
    const { agentId, reportId } = get();
    if (!agentId || !reportId || !identity() || get().submitting) return;
    const version = generation, controller = new AbortController(); write = controller;
    update({ pending: request, unknown: false, submitting: true, error: null });
    try {
      const receipt = await AgentTrialReviewsApi.submit(agentId, reportId, request, controller.signal);
      if (!current(version, agentId, reportId) || controller.signal.aborted) return;
      const actor = useAuthStore.getState().user?.id;
      if (!actor || !validReview(receipt, agentId, reportId, actor) || !matchesRequest(receipt, request)) throw new Error('invalidResponse');
      update({ pending: null, unknown: false, ready: false });
      if (get().state) set({ state: { ...get().state!, reviews: [receipt, ...get().state!.reviews.filter(row => row.id !== receipt.id)] } });
      await get().refresh();
    } catch (error) {
      if (!current(version, agentId, reportId) || controller.signal.aborted) return;
      const rejected = error instanceof APIError && [400, 401, 403, 404, 409, 422].includes(error.status);
      update({ error: errorKind(error), ready: false, unknown: !rejected, ...(rejected ? { pending: null } : {}) });
    } finally {
      if (current(version, agentId, reportId) && write === controller) update({ submitting: false });
      if (write === controller) write = null;
    }
  };
  return {
    ...empty, identity: null, agentId: null, reportId: null, question: '', catalog: [], state: null, sessions: {},
    ready: false, loading: false, submitting: false, error: null,
    select: async (agentId, reportId, question) => {
      if (get().agentId && get().pending && get().submitting) update({ unknown: true });
      abort();
      const scope = identity();
      if (!scope) { set({ identity: null, agentId: null, reportId: null }); return; }
      const saved = get().sessions[sessionKey(agentId, reportId)] ?? empty;
      const version = generation, controller = new AbortController(); read = controller;
      set({ ...saved, identity: scope, agentId, reportId, question, state: null, catalog: [],
        ready: false, loading: true, submitting: false, error: null });
      try {
        const [catalog, state] = await Promise.all([AgentTrialReviewsApi.cases(agentId, controller.signal),
          reportId ? AgentTrialReviewsApi.state(agentId, reportId, controller.signal) : Promise.resolve(null)]);
        if (!current(version, agentId, reportId) || controller.signal.aborted) return;
        acceptCatalog(catalog);
        if (state && reportId) { validateState(state, agentId, reportId); accept(state); }
        else set({ ready: true });
      } catch (error) {
        if (current(version, agentId, reportId) && !controller.signal.aborted) set({ ready: false, error: errorKind(error) });
      } finally {
        if (current(version, agentId, reportId) && read === controller) set({ loading: false });
        if (read === controller) read = null;
      }
    },
    refresh: async () => {
      const { agentId, reportId } = get();
      if (!agentId || !identity() || get().loading) return;
      if (!reportId) { await get().select(agentId, reportId, get().question); return; }
      const version = generation, controller = new AbortController(); read = controller;
      set({ ready: false, loading: true, error: null });
      try {
        const [catalog, state] = await Promise.all([AgentTrialReviewsApi.cases(agentId, controller.signal),
          AgentTrialReviewsApi.state(agentId, reportId, controller.signal)]);
        if (!current(version, agentId, reportId) || controller.signal.aborted) return;
        acceptCatalog(catalog);
        validateState(state, agentId, reportId); accept(state);
      } catch (error) {
        if (current(version, agentId, reportId) && !controller.signal.aborted) set({ ready: false, error: errorKind(error) });
      } finally {
        if (current(version, agentId, reportId) && read === controller) set({ loading: false });
        if (read === controller) read = null;
      }
    },
    submit: async (caseId, decision, checks, note) => {
      const state = get().state;
      if (!identity() || !get().ready || get().loading || get().submitting || get().pending || !state ||
        !state.available_cases.some(row => row.id === caseId) || note.length > 1000) return;
      if (decision === 'passed' && (!state.can_review || Date.parse(state.valid_until) <= Date.now() ||
        !Object.values(checks).every(value => value === true))) { set({ error: 'invalid' }); return; }
      const latest = state.reviews.filter(row => row.case.id === caseId).sort((left, right) => right.revision - left.revision)[0];
      await send({ request_key: crypto.randomUUID(), case_id: caseId, decision, checks: { ...checks }, note,
        expected_latest_review_id: latest?.id ?? null });
    },
    retry: async () => {
      if (!get().pending || !identity() || get().submitting || get().loading) return;
      await get().refresh();
      const pending = get().pending;
      if (get().ready && pending) await send(pending);
    },
    leave: agentId => {
      if (get().agentId !== agentId) return;
      if (get().pending && get().submitting) update({ unknown: true });
      abort(); set({ ...empty, identity: null, agentId: null, reportId: null, question: '', catalog: [], state: null,
        ready: false, loading: false, submitting: false, error: null });
    },
  };
});
useAuthStore.subscribe((next, previous) => {
  if (next.user?.id === previous.user?.id && next.user?.project_id === previous.user?.project_id && next.user?.role === previous.user?.role) return;
  abort(); useAgentTrialReviewStore.setState({ ...empty, identity: null, agentId: null, reportId: null, question: '', catalog: [],
    state: null, sessions: {}, ready: false, loading: false, submitting: false, error: null });
});
