import type {
  WorkflowEventSubscription, WorkflowEventSubscriptionUpdate, WorkflowEventDeliveryList,
  WorkflowEventTestContext, WorkflowEventSubscriptionState,
} from '@/types/workflowEvents';

interface SubscriptionApi {
  getEventSubscription(id: string, signal?: AbortSignal): Promise<WorkflowEventSubscription>;
  updateEventSubscription(id: string, request: WorkflowEventSubscriptionUpdate, signal?: AbortSignal): Promise<WorkflowEventSubscription>;
  getEventTestContext(id: string, signal?: AbortSignal): Promise<WorkflowEventTestContext>;
  listEventDeliveries(id: string, signal?: AbortSignal): Promise<WorkflowEventDeliveryList>;
}

export function newEventSubscriptionState(): WorkflowEventSubscriptionState {
  return { ownerKey: null, workflowId: null, subscription: null, context: null, deliveries: [],
    loading: false, saving: false, error: '', contextError: '', deliveryError: '' };
}

export function createWorkflowEventSubscriptionActions(
  set: (update: Partial<WorkflowEventSubscriptionState>) => void,
  get: () => WorkflowEventSubscriptionState,
  dependencies: { api: SubscriptionApi; currentOwner: () => string | null; translate: (key: string) => string },
) {
  const { api, currentOwner, translate } = dependencies;
  let generation = 0;
  let controller: AbortController | null = null;
  const valid = (id: string, epoch: number) => Boolean(
    epoch === generation && get().workflowId === id && get().ownerKey === currentOwner()
    && currentOwner() && controller && !controller.signal.aborted,
  );
  const message = (error: unknown, key: string) => error instanceof Error ? error.message : translate(key);
  const stop = () => { generation += 1; controller?.abort(); controller = null; set(newEventSubscriptionState()); };
  const validate = (subscription: WorkflowEventSubscription, id: string) => {
    if (subscription.workflow_id !== id || subscription.event_type !== 'staff.session.closed') {
      throw new Error(translate('workflowEvents.subscription.invalidResponse'));
    }
    return subscription;
  };

  async function readSubscription(id: string, epoch: number) {
    try {
      const subscription = validate(await api.getEventSubscription(id, controller!.signal), id);
      if (valid(id, epoch)) set({ subscription });
    } catch (error: unknown) {
      if (valid(id, epoch)) set({ subscription: null, error: message(error, 'workflowEvents.subscription.loadFailed') });
    }
  }

  async function readContext(id: string, epoch: number) {
    try {
      const context = await api.getEventTestContext(id, controller!.signal);
      if (!valid(id, epoch)) return;
      if (context.workflow_id !== id || context.status !== 'active') throw new Error(translate('workflowEvents.invalidContext'));
      set({ context, contextError: '' });
    } catch (error: unknown) {
      if (valid(id, epoch)) set({ context: null, contextError: message(error, 'workflowEvents.contextFailed') });
    }
  }

  async function readDeliveries(id: string, epoch: number) {
    try {
      const response = await api.listEventDeliveries(id, controller!.signal);
      if (valid(id, epoch)) set({ deliveries: response.items, deliveryError: '' });
    } catch (error: unknown) {
      if (valid(id, epoch)) set({ deliveries: [], deliveryError: message(error, 'workflowEvents.subscription.deliveriesFailed') });
    }
  }

  async function load(id: string, epoch: number) {
    if (!valid(id, epoch)) return;
    set({ loading: true, error: '' });
    // Subscription state must remain manageable even when publication validation fails.
    await Promise.allSettled([readSubscription(id, epoch), readContext(id, epoch), readDeliveries(id, epoch)]);
    if (valid(id, epoch)) set({ loading: false });
  }

  return {
    activate: (ownerKey: string, workflowId: string) => {
      stop();
      if (ownerKey !== currentOwner()) return () => {};
      controller = new AbortController();
      set({ ownerKey, workflowId });
      const epoch = generation;
      void load(workflowId, epoch);
      return () => { if (epoch === generation) stop(); };
    },
    refresh: async () => {
      const { workflowId, loading, saving } = get();
      if (workflowId && !loading && !saving) await load(workflowId, generation);
    },
    setEnabled: async (enabled: boolean) => {
      const { workflowId: id, subscription, context, loading, saving } = get(), epoch = generation;
      if (!id || !valid(id, epoch) || !subscription || loading || saving) return;
      if (enabled && (!context || context.event_type !== 'staff.session.closed')) return;
      const request: WorkflowEventSubscriptionUpdate = { enabled, expected_revision: subscription.revision };
      if (enabled && context) {
        request.expected_release_id = context.release_id;
        request.expected_release_digest = context.release_digest;
      }
      set({ saving: true, error: '' });
      try {
        const result = validate(await api.updateEventSubscription(id, request, controller!.signal), id);
        if (valid(id, epoch)) set({ subscription: result });
      } catch (error: unknown) {
        if (!valid(id, epoch)) return;
        const errorMessage = message(error, 'workflowEvents.subscription.saveFailed');
        // A lost response may already have changed the server revision. Read it before retrying.
        set({ subscription: null, error: errorMessage });
        await readSubscription(id, epoch);
        if (valid(id, epoch)) set({ error: errorMessage });
      } finally { if (valid(id, epoch)) set({ saving: false }); }
    },
  };
}
