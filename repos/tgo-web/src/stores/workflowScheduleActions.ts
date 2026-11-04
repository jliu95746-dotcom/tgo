import type { ScheduleUiState, WorkflowScheduleApi, ScheduleCommand, ScheduleReceipt,
  SchedulePreviewRequest } from '../types/workflowSchedules';
import { readScheduleCommand } from '../utils/workflowScheduleForm.ts';

export function newScheduleState(): ScheduleUiState {
  return { ownerKey: null, workflowId: null, state: null, history: null, pending: null,
    preview: null, loading: false, saving: false, previewing: false, error: '', historyError: '', previewError: '', notice: '' };
}
export function createScheduleActions(
  set: (update: Partial<ScheduleUiState>) => void, get: () => ScheduleUiState,
  dependencies: { api: WorkflowScheduleApi; currentOwner: () => string | null;
    createId: () => string; translate: (key: string) => string;
    storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> },
) {
  const { api, currentOwner, translate: t, storage } = dependencies;
  let epoch = 0, previewEpoch = 0, readable = true;
  let controller: AbortController | null = null;
  const valid = (generation: number) => generation === epoch && controller && !controller.signal.aborted
    && get().ownerKey && get().ownerKey === currentOwner();
  const key = () => `workflow-schedule-command:${get().ownerKey}:${get().workflowId}`;
  const message = (error: unknown) => error instanceof Error ? error.message : t('workflowSchedule.loadFailed');
  function stop() { epoch++; previewEpoch++; controller?.abort(); controller = null; set(newScheduleState()); }
  async function load(generation: number, offset = 0) {
    if (!valid(generation)) return;
    const id = get().workflowId!;
    set({ loading: true, error: '', historyError: '' });
    await Promise.allSettled([
      (async () => {
        try {
          const state = await api.getState(id, controller!.signal);
          if (!valid(generation)) return;
          if (state.schedule && (state.schedule.workflow_id !== id || state.schedule.project_id !== get().ownerKey!.split(':')[0])) {
            throw new Error(t('workflowSchedule.invalidResponse'));
          }
          set({ state });
        } catch (error) { if (valid(generation)) set({ state: null, error: message(error) }); }
      })(),
      (async () => {
        try {
          const history = await api.getHistory(id, offset, controller!.signal);
          if (valid(generation)) set({ history });
        } catch (error) { if (valid(generation)) set({ history: null, historyError: message(error) }); }
      })(),
    ]);
    if (valid(generation)) set({ loading: false });
  }
  async function confirm(receipt: ScheduleReceipt, command: ScheduleCommand, generation: number) {
    if (!valid(generation)) return;
    const plan = receipt.schedule;
    if (receipt.request_id !== command.request.request_id || receipt.operation !== command.operation
      || plan.workflow_id !== get().workflowId || plan.project_id !== get().ownerKey!.split(':')[0]
      || plan.revision !== command.request.expected_revision + 1
      || (command.operation === 'disable' && plan.enabled)
      || (command.operation === 'save' && (plan.enabled !== command.request.enabled
        || plan.release_id !== command.request.expected_release_id || plan.release_digest !== command.request.expected_release_digest))) {
      throw new Error(t('workflowSchedule.invalidResponse'));
    }
    storage.removeItem(key());
    set({ pending: null, notice: t('workflowSchedule.confirmed') });
    // Original receipts are immutable; they are never the current plan state.
    await load(generation);
  }
  async function dispatch(command: ScheduleCommand) {
    const generation = epoch, id = get().workflowId;
    if (!id || !valid(generation) || get().saving) return;
    set({ saving: true, error: '', notice: '' });
    try {
      const result = command.operation === 'save'
        ? await api.save(id, command.request, controller!.signal)
        : await api.disable(id, command.request, controller!.signal);
      await confirm(result, command, generation);
    } catch (error) { if (valid(generation)) set({ error: message(error) }); }
    finally { if (valid(generation)) set({ saving: false }); }
  }
  async function begin(command: ScheduleCommand) {
    if (!valid(epoch) || !readable || get().pending || get().loading || get().saving) return;
    try { storage.setItem(key(), JSON.stringify(command)); }
    catch { set({ error: t('workflowSchedule.storageFailed') }); return; }
    set({ pending: command });
    await dispatch(command);
  }
  return {
    activate(ownerKey: string, workflowId: string) {
      stop(); readable = true;
      if (ownerKey !== currentOwner()) return () => {};
      controller = new AbortController();
      set({ ownerKey, workflowId });
      const generation = epoch;
      void load(generation);
      try {
        const raw = storage.getItem(key()), pending = readScheduleCommand(raw);
        if (raw && !pending) throw new Error('invalid');
        set({ pending });
      } catch { readable = false; set({ error: t('workflowSchedule.storageFailed') }); }
      return () => { if (epoch === generation) stop(); };
    },
    refresh: async (offset = 0) => { if (!get().loading && !get().saving) await load(epoch, offset); },
    async requestPreview(request: SchedulePreviewRequest) {
      const generation = epoch, version = ++previewEpoch, id = get().workflowId;
      if (!id || !valid(generation)) return;
      set({ preview: null, previewError: '', previewing: true });
      try {
        const result = await api.preview(id, request, controller!.signal);
        if (valid(generation) && version === previewEpoch) {
          if (result.cron_expression !== request.cron_expression.trim().replace(/\s+/g, ' ') || result.timezone !== request.timezone) {
            throw new Error(t('workflowSchedule.invalidResponse'));
          }
          set({ preview: result });
        }
      } catch (error) { if (valid(generation) && version === previewEpoch) set({ previewError: message(error) }); }
      finally { if (valid(generation) && version === previewEpoch) set({ previewing: false }); }
    },
    clearPreview() { previewEpoch++; set({ preview: null, previewError: '', previewing: false }); },
    async save(enabled: boolean) {
      const state = get().state, release = state?.current_release;
      if (!release || (enabled && (!state?.execution_available || !state?.runtime?.ready))) return;
      await begin({ operation: 'save', request: { request_id: dependencies.createId(), expected_revision: state?.schedule?.revision ?? 0,
        expected_release_id: release.release_id, expected_release_digest: release.definition_digest, enabled } });
    },
    async disable() {
      const plan = get().state?.schedule;
      if (plan) await begin({ operation: 'disable', request: { request_id: dependencies.createId(), expected_revision: plan.revision } });
    },
    async recover() {
      const command = get().pending, generation = epoch, id = get().workflowId;
      if (!command || !id || !valid(generation) || get().saving) return;
      set({ saving: true, error: '' });
      try { await confirm(await api.getCommand(id, command.request.request_id, controller!.signal), command, generation); }
      catch (error) { if (valid(generation)) set({ error: message(error) }); }
      finally { if (valid(generation)) set({ saving: false }); }
    },
    retry: async () => { const command = get().pending; if (command) await dispatch(command); },
    async forget() {
      if (!valid(epoch) || get().saving || get().loading) return;
      try { storage.removeItem(key()); readable = true; set({ pending: null, error: '', notice: t('workflowSchedule.forgotten') }); await load(epoch); }
      catch { set({ error: t('workflowSchedule.storageFailed') }); }
    },
  };
}
