import { create } from 'zustand';
import { BusinessScenariosApi } from '@/services/businessScenariosApi';
import type { ScenarioAgentReceipt, ScenarioDraft, ScenarioDraftInput } from '@/types/scenarioDrafts';
import { useAuthStore } from './authStore';
import { useScenarioDraftStore } from './scenarioDraftStore';

interface ScenarioAgentState {
  receipt: ScenarioAgentReceipt | null;
  busy: boolean;
  failed: boolean;
  prepare: (input: ScenarioDraftInput) => Promise<ScenarioAgentReceipt | null>;
  cancel: () => void;
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).sort(([first], [second]) => first.localeCompare(second)).map(([key, item]) => [key, canonical(item)]),
  );
  return value;
}

export function matchesSavedAgent(input: ScenarioDraftInput, draft: ScenarioDraft | null): boolean {
  return Boolean(draft && input.template_id === draft.template_id && input.module === draft.module
    && input.template_version === draft.template_version && input.channel === draft.channel
    && JSON.stringify(canonical([input.business, input.bindings, input.agent_settings]))
      === JSON.stringify(canonical([draft.business, draft.bindings, draft.agent_settings])));
}

let generation = 0;
let controller: AbortController | null = null;
const empty = { receipt: null, busy: false, failed: false };
export const useScenarioAgentStore = create<ScenarioAgentState>((set, get) => ({
  ...empty,
  cancel: () => { generation++; controller?.abort(); controller = null; set({ busy: false }); },
  prepare: async input => {
    const user = useAuthStore.getState().user;
    if (get().busy || user?.role !== 'admin' || !user.project_id) return null;
    const saved = useScenarioDraftStore.getState().current;
    const receipt = get().receipt;
    // Navigation confirms an already saved snapshot; activation independently checks live policies.
    if (saved?.project_id === user.project_id && saved.created_by === user.id
      && matchesSavedAgent(input, saved) && receipt?.project_id === user.project_id
      && receipt.created_by === user.id && receipt.draft_id === saved.id
      && receipt.revision === saved.revision && receipt.agent_id
      && receipt.is_active === false && receipt.can_enable === false) return receipt;
    const version = ++generation;
    controller?.abort();
    const requestController = new AbortController();
    controller = requestController;
    set({ busy: true, failed: false, receipt: null });
    try {
      let current = useScenarioDraftStore.getState().current;
      if (!matchesSavedAgent(input, current)) {
        if (!await useScenarioDraftStore.getState().save(input)) throw new Error('Save setup first');
        if (version !== generation) return null;
        current = useScenarioDraftStore.getState().current;
      }
      if (!current || current.project_id !== user.project_id || current.created_by !== user.id
        || !matchesSavedAgent(input, current)) throw new Error('Setup identity mismatch');
      const receipt = await BusinessScenariosApi.saveDraftAgent(current.id, current.revision, requestController.signal);
      if (version !== generation) return null;
      if (receipt.project_id !== user.project_id || receipt.created_by !== user.id
        || receipt.draft_id !== current.id || receipt.revision !== current.revision
        || receipt.is_active !== false || receipt.can_enable !== false || !receipt.agent_id
        || useScenarioDraftStore.getState().current?.revision !== current.revision) throw new Error('Invalid setup receipt');
      set({ receipt });
      return receipt;
    } catch { if (version === generation) set({ failed: true }); return null; }
    finally { if (version === generation) { controller = null; set({ busy: false }); } }
  },
}));

useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id
    || state.user?.role !== previous.user?.role) {
    useScenarioAgentStore.getState().cancel();
    useScenarioAgentStore.setState(empty);
  }
});
