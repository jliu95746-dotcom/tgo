import type { WorkflowExecution, WorkflowJsonValue } from '@/types/workflow';
import type {
  WorkflowEventAccepted, WorkflowEventTestContext, WorkflowEventTestRequest,
  WorkflowEventTestSession, WorkflowEventTestState,
} from '@/types/workflowEvents';

interface EventTestApi {
  getEventTestContext(id: string, signal?: AbortSignal): Promise<WorkflowEventTestContext>;
  sendEventTest(id: string, request: WorkflowEventTestRequest, signal?: AbortSignal): Promise<WorkflowEventAccepted>;
  getExecution(id: string, signal?: AbortSignal): Promise<WorkflowExecution>;
  cancelExecution(id: string, signal?: AbortSignal): Promise<void>;
}

type SetState = (update: Partial<WorkflowEventTestState> | ((state: WorkflowEventTestState) => Partial<WorkflowEventTestState>)) => void;

export function newEventTestSession(): WorkflowEventTestSession {
  return { context: null, jsonInput: '{\n  "text": ""\n}', attempt: null,
    accepted: null, execution: null, loadingContext: false, sending: false,
    refreshing: false, cancelling: false, rejected: false, error: '' };
}

export function eventTestTerminal(status?: string): boolean {
  return status !== undefined && ['completed', 'failed', 'rejected', 'expired', 'cancelled'].includes(status);
}

function isJsonValue(value: unknown): value is WorkflowJsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  return typeof value === 'object' && Object.values(value).every(isJsonValue);
}

export function createWorkflowEventTestActions(
  set: SetState, get: () => WorkflowEventTestState,
  dependencies: { api: EventTestApi; currentOwner: () => string | null;
    createId: () => string; translate: (key: string) => string },
) {
  const { api, currentOwner, createId, translate } = dependencies;
  let generation = 0;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clearTimer = () => { if (timer !== null) clearTimeout(timer); timer = null; };
  const current = () => {
    const state = get();
    return state.activeWorkflowId && state.ownerKey === currentOwner()
      ? state.sessions[state.activeWorkflowId] : undefined;
  };
  const valid = (id: string, epoch: number) => Boolean(
    epoch === generation && get().activeWorkflowId === id && get().ownerKey === currentOwner()
    && currentOwner() && controller && !controller.signal.aborted,
  );
  const change = (id: string, patch: Partial<WorkflowEventTestSession>) => set(state => ({
    sessions: { ...state.sessions, [id]: { ...state.sessions[id], ...patch } },
  }));
  const message = (error: unknown, key: string) => error instanceof Error ? error.message : translate(key);
  const stop = () => {
    generation += 1; clearTimer(); controller?.abort(); controller = null;
    const id = get().activeWorkflowId;
    if (id && get().sessions[id]) change(id, { loadingContext: false, sending: false, refreshing: false, cancelling: false });
    set({ activeWorkflowId: null });
  };

  async function loadContext(id: string, epoch: number) {
    if (!valid(id, epoch)) return;
    change(id, { loadingContext: true, error: '' });
    try {
      const context = await api.getEventTestContext(id, controller!.signal);
      if (!valid(id, epoch)) return;
      if (context.workflow_id !== id || context.status !== 'active') throw new Error(translate('workflowEvents.invalidContext'));
      change(id, { context });
    } catch (error: unknown) {
      if (valid(id, epoch)) change(id, { context: null, error: message(error, 'workflowEvents.contextFailed') });
    } finally { if (valid(id, epoch)) change(id, { loadingContext: false }); }
  }

  async function readStatus(id: string, epoch: number, cancelError = '') {
    const accepted = get().sessions[id]?.accepted;
    if (!accepted || !valid(id, epoch)) return;
    clearTimer(); change(id, { refreshing: true });
    try {
      const execution = await api.getExecution(accepted.execution_id, controller!.signal);
      if (!valid(id, epoch)) return;
      if (execution.id !== accepted.execution_id || execution.workflow_id !== id) throw new Error(translate('workflowEvents.invalidResult'));
      change(id, { execution, error: eventTestTerminal(execution.status) ? '' : cancelError });
      if (!eventTestTerminal(execution.status)) timer = setTimeout(() => { void readStatus(id, epoch, cancelError); }, 1500);
    } catch (error: unknown) {
      if (valid(id, epoch)) change(id, { error: message(error, 'workflowEvents.statusFailed') });
    } finally { if (valid(id, epoch)) change(id, { refreshing: false }); }
  }

  return {
    activate: (ownerKey: string, workflowId: string) => {
      stop();
      if (ownerKey !== currentOwner()) return () => {};
      const sessions = get().ownerKey === ownerKey ? get().sessions : {};
      set({ ownerKey, activeWorkflowId: workflowId,
        sessions: { ...sessions, [workflowId]: sessions[workflowId] ?? newEventTestSession() } });
      controller = new AbortController();
      const epoch = generation;
      const session = get().sessions[workflowId];
      if (session.accepted) void readStatus(workflowId, epoch);
      else if (!session.attempt) void loadContext(workflowId, epoch);
      return () => { if (epoch === generation) stop(); };
    },
    setInput: (jsonInput: string) => {
      const session = current(), id = get().activeWorkflowId;
      if (session && id && !session.attempt && !session.sending) change(id, { jsonInput, error: '' });
    },
    reloadContext: async () => {
      const session = current(), id = get().activeWorkflowId;
      if (session && id && !session.attempt && !session.loadingContext) await loadContext(id, generation);
    },
    send: async () => {
      const session = current(), id = get().activeWorkflowId, epoch = generation;
      if (!session || !id || !valid(id, epoch) || !session.context || session.loadingContext
        || session.sending || session.accepted || session.rejected) return;
      let attempt = session.attempt;
      if (!attempt) {
        try {
          if (new TextEncoder().encode(session.jsonInput).length > 60000) throw new Error(translate('workflowEvents.tooLarge'));
          let data: unknown;
          try { data = JSON.parse(session.jsonInput); } catch { throw new Error(translate('workflowEvents.invalidJson')); }
          if (!data || typeof data !== 'object' || Array.isArray(data) || !isJsonValue(data)) throw new Error(translate('workflowEvents.invalidJson'));
          attempt = { request_id: createId(), event_type: session.context.event_type,
            expected_release_id: session.context.release_id, expected_release_digest: session.context.release_digest,
            data: data as Record<string, WorkflowJsonValue> };
        } catch (error: unknown) { change(id, { error: message(error, 'workflowEvents.invalidJson') }); return; }
      }
      // Preserve this exact request before sending: a lost response must never create a new event.
      change(id, { attempt, sending: true, error: '', rejected: false });
      try {
        const accepted = await api.sendEventTest(id, attempt, controller!.signal);
        if (!valid(id, epoch)) return;
        change(id, { accepted });
        await readStatus(id, epoch);
      } catch (error: unknown) {
        if (!valid(id, epoch)) return;
        const status = typeof error === 'object' && error !== null && 'status' in error ? error.status : null;
        const rejected = typeof status === 'number' && [400, 401, 403, 404, 409, 413, 422, 429].includes(status);
        change(id, { error: message(error, 'workflowEvents.sendFailed'), rejected });
      } finally { if (valid(id, epoch)) change(id, { sending: false }); }
    },
    refresh: async () => {
      const session = current(), id = get().activeWorkflowId;
      if (session && id && !session.sending && !session.refreshing && !session.cancelling) await readStatus(id, generation);
    },
    cancel: async () => {
      const session = current(), id = get().activeWorkflowId, epoch = generation;
      if (!session?.accepted || !id || !valid(id, epoch) || session.sending || session.refreshing
        || session.cancelling || eventTestTerminal(session.execution?.status)) return;
      clearTimer(); change(id, { cancelling: true, error: '' });
      let cancelError = '';
      try { await api.cancelExecution(session.accepted.execution_id, controller!.signal); }
      catch (error: unknown) { cancelError = message(error, 'workflowEvents.cancelFailed'); }
      if (!valid(id, epoch)) return;
      // Cancellation may lose to completion. Only the persisted record determines the displayed state.
      await readStatus(id, epoch, cancelError);
      if (valid(id, epoch)) change(id, { cancelling: false });
    },
    reset: () => {
      const session = current(), id = get().activeWorkflowId;
      if (!session || !id || session.loadingContext || session.sending || session.refreshing || session.cancelling
        || (session.attempt && !session.rejected && !eventTestTerminal(session.execution?.status))) return;
      clearTimer();
      change(id, newEventTestSession());
      void loadContext(id, generation);
    },
  };
}
