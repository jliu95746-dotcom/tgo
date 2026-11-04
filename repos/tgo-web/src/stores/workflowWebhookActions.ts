import type { WebhookCommand, WebhookReceipt, WebhookUiState, WorkflowWebhookApi } from '../types/workflowWebhooks';

export function newWebhookState(): WebhookUiState {
  return { ownerKey: null, workflowId: null, state: null, history: null, pending: null, credential: null,
    storageReady: true, loading: false, saving: false, error: '', historyError: '', notice: '' };
}
function readCommand(raw: string | null): WebhookCommand | null {
  if (!raw) return null;
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== 'object') throw Error('invalid');
  const command = value as Record<string, unknown>;
  if (Object.keys(command).sort().join() !== 'operation,request' || !command.request || typeof command.request !== 'object') throw Error('invalid');
  const request = command.request as Record<string, unknown>;
  const keys = command.operation === 'save' ? 'enabled,expected_release_digest,expected_release_id,expected_revision,request_id' : 'expected_revision,request_id';
  if (!['save', 'disable', 'rotate'].includes(String(command.operation)) || Object.keys(request).sort().join() !== keys
    || typeof request.request_id !== 'string' || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(request.request_id)
    || !Number.isInteger(request.expected_revision) || Number(request.expected_revision) < (command.operation === 'save' ? 0 : 1)
    || Number(request.expected_revision) > 2147483646) throw Error('invalid');
  if (command.operation === 'save' && (typeof request.enabled !== 'boolean'
    || typeof request.expected_release_id !== 'string' || !request.expected_release_id || request.expected_release_id.length > 128
    || typeof request.expected_release_digest !== 'string' || !/^[a-f0-9]{64}$/.test(request.expected_release_digest))) throw Error('invalid');
  return command as unknown as WebhookCommand;
}
export function createWebhookActions(
  set: (update: Partial<WebhookUiState>) => void, get: () => WebhookUiState,
  dependencies: { api: WorkflowWebhookApi; currentOwner: () => string | null; createId: () => string;
    translate: (key: string) => string; canSave?: () => boolean;
    storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> },
) {
  const { api, currentOwner, translate: t, storage } = dependencies;
  let epoch = 0, readable = true;
  let controller: AbortController | null = null;
  const valid = (generation: number) => generation === epoch && controller && !controller.signal.aborted
    && get().ownerKey && get().ownerKey === currentOwner();
  const key = () => `workflow-webhook-command:${get().ownerKey}:${get().workflowId}`;
  // Server errors must not accidentally copy a credential into a persistent error panel.
  const failure = (error: unknown, name = 'workflowWebhook.loadFailed') => {
    const status = error && typeof error === 'object' && 'status' in error ? error.status : null;
    return t(name) + (typeof status === 'number' && Number.isInteger(status) ? ` (HTTP ${status})` : '');
  };
  function stop() { epoch++; controller?.abort(); controller = null; set(newWebhookState()); }
  async function load(generation: number, offset = 0) {
    if (!valid(generation)) return;
    const id = get().workflowId!, project = get().ownerKey!.split(':')[0];
    set({ loading: true, error: readable ? '' : t('workflowWebhook.storageFailed'), historyError: '', credential: null });
    await Promise.allSettled([
      (async () => {
        try {
          const state = await api.getState(id, controller!.signal);
          if (!valid(generation)) return;
          if (state.webhook && (state.webhook.workflow_id !== id || state.webhook.project_id !== project)) throw Error('scope');
          set({ state });
        } catch (error) { if (valid(generation)) set({ state: null, error: failure(error) }); }
      })(),
      (async () => {
        try {
          const history = await api.getHistory(id, offset, controller!.signal);
          if (!valid(generation)) return;
          if (history.workflow_id !== id || history.project_id !== project || history.offset !== offset) {
            set({ history: null, historyError: t('workflowWebhook.invalidResponse') }); return;
          }
          set({ history });
        } catch (error) { if (valid(generation)) set({ history: null, historyError: failure(error) }); }
      })(),
    ]);
    if (valid(generation)) set({ loading: false });
  }
  async function confirm(receipt: WebhookReceipt, command: WebhookCommand, generation: number) {
    if (!valid(generation)) return;
    const hook = receipt.webhook;
    if (receipt.request_id !== command.request.request_id || receipt.operation !== command.operation
      || hook.workflow_id !== get().workflowId || hook.project_id !== get().ownerKey!.split(':')[0]
      || hook.revision !== command.request.expected_revision + 1
      || (command.operation !== 'save' && hook.enabled)
      || (command.operation === 'save' && (hook.enabled !== command.request.enabled
        || hook.release_id !== command.request.expected_release_id || hook.release_digest !== command.request.expected_release_digest))) throw Error('invalid');
    if (!['not_issued', 'available', 'superseded', 'unavailable'].includes(receipt.credential_status)
      || (receipt.credential_status === 'available'
        && (typeof receipt.credential?.secret !== 'string' || !receipt.credential.secret || receipt.credential.secret.length > 4096))
      || (receipt.credential_status !== 'available' && receipt.credential != null)) throw Error('invalid');
    storage.removeItem(key());
    set({ pending: null, notice: t('workflowWebhook.confirmed') });
    await load(generation);
    if (!valid(generation)) return;
    const current = get().state?.webhook;
    if (receipt.credential_status === 'available' && receipt.credential?.secret && current
      && current.id === hook.id && current.credential_revision === hook.credential_revision && current.key_id === hook.key_id) {
      set({ credential: { secret: receipt.credential.secret, hookId: hook.id, revision: hook.credential_revision, keyId: hook.key_id } });
    } else if (receipt.credential_status === 'superseded' || receipt.credential_status === 'unavailable') {
      set({ notice: t(`workflowWebhook.credentials.${receipt.credential_status}`) });
    }
  }
  async function dispatch(command: WebhookCommand) {
    const generation = epoch, id = get().workflowId;
    if (!id || !valid(generation) || get().saving) return;
    set({ saving: true, error: '', notice: '', credential: null });
    try {
      const result = command.operation === 'save' ? await api.save(id, command.request, controller!.signal)
        : command.operation === 'disable' ? await api.disable(id, command.request, controller!.signal)
          : await api.rotate(id, command.request, controller!.signal);
      await confirm(result, command, generation);
    } catch (error) { if (valid(generation)) set({ error: failure(error, 'workflowWebhook.commandUnknown') }); }
    finally { if (valid(generation)) set({ saving: false }); }
  }
  async function begin(command: WebhookCommand) {
    if (!valid(epoch) || !readable || get().pending || get().loading || get().saving) return;
    try { storage.setItem(key(), JSON.stringify(command)); }
    catch { set({ error: t('workflowWebhook.storageFailed') }); return; }
    set({ pending: command }); await dispatch(command);
  }
  return {
    activate(ownerKey: string, workflowId: string) {
      stop(); readable = true;
      if (ownerKey !== currentOwner()) return () => {};
      controller = new AbortController(); set({ ownerKey, workflowId });
      const generation = epoch;
      void load(generation);
      try { set({ pending: readCommand(storage.getItem(key())) }); }
      catch { readable = false; set({ storageReady: false, error: t('workflowWebhook.storageFailed') }); }
      return () => { if (epoch === generation) stop(); };
    },
    reset: stop,
    hideCredential: () => set({ credential: null }),
    refresh: async (offset = 0) => { if (!get().loading && !get().saving) await load(epoch, offset); },
    async save(enabled: boolean) {
      const state = get().state, release = state?.current_release;
      if (!release || !state.registration_available || (enabled && !state.execution_available) || dependencies.canSave?.() === false) return;
      await begin({ operation: 'save', request: { request_id: dependencies.createId(), expected_revision: state.webhook?.revision ?? 0,
        expected_release_id: release.release_id, expected_release_digest: release.definition_digest, enabled } });
    },
    async disable() {
      const hook = get().state?.webhook;
      if (hook) await begin({ operation: 'disable', request: { request_id: dependencies.createId(), expected_revision: hook.revision } });
    },
    async rotate() {
      const hook = get().state?.webhook;
      if (hook && get().state?.registration_available) await begin({ operation: 'rotate', request: { request_id: dependencies.createId(), expected_revision: hook.revision } });
    },
    async recover() {
      const command = get().pending, generation = epoch, id = get().workflowId;
      if (!command || !id || !valid(generation) || get().saving) return;
      set({ saving: true, error: '', credential: null });
      try { await confirm(await api.getCommand(id, command.request.request_id, controller!.signal), command, generation); }
      catch (error) { if (valid(generation)) set({ error: failure(error, 'workflowWebhook.commandUnknown') }); }
      finally { if (valid(generation)) set({ saving: false }); }
    },
    retry: async () => { const command = get().pending; if (command) await dispatch(command); },
  };
}
