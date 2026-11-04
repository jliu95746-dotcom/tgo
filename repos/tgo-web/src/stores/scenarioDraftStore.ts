import { create } from 'zustand';
import { BusinessScenariosApi } from '@/services/businessScenariosApi';
import type { ScenarioModule } from '@/types/businessScenarios';
import type { ScenarioDraft, ScenarioDraftCreate, ScenarioDraftInput, ScenarioStep, ScenarioTemplate } from '@/types/scenarioDrafts';
import { useAuthStore } from './authStore';

interface ScenarioDraftState {
  drafts: ScenarioDraft[];
  templates: ScenarioTemplate[];
  current: ScenarioDraft | null;
  pendingCreation: ScenarioDraftCreate | null;
  busy: boolean;
  failed: boolean;
  step: ScenarioStep;
  setStep: (step: ScenarioStep) => void;
  startNew: () => void;
  list: (module: ScenarioModule) => Promise<void>;
  resume: (id: string) => Promise<ScenarioDraft | null>;
  save: (input: ScenarioDraftInput) => Promise<boolean>;
}

let generation = 0;
function ownsDraft(draft: ScenarioDraft): boolean {
  const user = useAuthStore.getState().user;
  return Boolean(user && draft.project_id === user.project_id && draft.created_by === user.id);
}
const empty = { drafts: [], templates: [], current: null, pendingCreation: null, busy: false, failed: false, step: 'scenario' as ScenarioStep };
export const useScenarioDraftStore = create<ScenarioDraftState>((set, get) => ({
  ...empty,
  setStep: step => set({ step }),
  startNew: () => {
    generation += 1;
    set({ current: null, pendingCreation: null, busy: false, failed: false, step: 'scenario' });
  },
  list: async module => {
    if (!useAuthStore.getState().user?.project_id || get().busy) return;
    const version = ++generation;
    set({ busy: true, failed: false });
    try {
      const [drafts, templates] = await Promise.all([BusinessScenariosApi.drafts(module), BusinessScenariosApi.templates(module)]);
      if (version !== generation) return;
      if (!drafts.every(draft => ownsDraft(draft) && draft.module === module)) throw new Error('Draft scope mismatch');
      if (!templates.every(template => template.module === module)) throw new Error('Template module mismatch');
      set({ drafts, templates });
    } catch { if (version === generation) set({ failed: true }); }
    finally { if (version === generation) set({ busy: false }); }
  },
  resume: async id => {
    if (get().busy || !useAuthStore.getState().user) return null;
    const version = ++generation;
    set({ busy: true, failed: false });
    try {
      const draft = await BusinessScenariosApi.draft(id);
      if (version !== generation) return null;
      if (!ownsDraft(draft) || draft.id !== id) throw new Error('Draft scope mismatch');
      set({ current: draft, pendingCreation: null, step: draft.step });
      return draft;
    } catch { if (version === generation) set({ failed: true }); return null; }
    finally { if (version === generation) set({ busy: false }); }
  },
  save: async input => {
    if (get().busy || !useAuthStore.getState().user?.project_id) return false;
    const version = ++generation;
    set({ busy: true, failed: false });
    try {
      let current = get().current;
      if (current && (current.module !== input.module || current.template_id !== input.template_id || current.template_version !== input.template_version)) {
        throw new Error('Choose a new setup draft before changing templates');
      }
      if (!current) {
        // Preserve the exact creation request after an unknown network outcome.
        const pending = get().pendingCreation ?? {
          idempotency_key: crypto.randomUUID(), module: input.module, template_id: input.template_id,
          template_version: input.template_version, channel: input.channel, business: input.business, bindings: input.bindings,
          agent_settings: input.agent_settings,
        };
        set({ pendingCreation: structuredClone(pending) });
        current = await BusinessScenariosApi.createDraft(pending);
        if (version !== generation) return false;
        if (!ownsDraft(current) || current.module !== input.module || current.template_id !== input.template_id) throw new Error('Draft scope mismatch');
        set({ current, pendingCreation: null });
      }
      const saved = await BusinessScenariosApi.updateDraft(current.id, {
        expected_revision: current.revision, step: input.step, channel: input.channel,
        business: input.business, bindings: input.bindings, agent_settings: input.agent_settings,
      });
      if (version !== generation) return false;
      if (!ownsDraft(saved) || saved.id !== current.id) throw new Error('Draft scope mismatch');
      set({ current: saved, drafts: [saved, ...get().drafts.filter(draft => draft.id !== saved.id)] });
      return true;
    } catch { if (version === generation) set({ failed: true }); return false; }
    finally { if (version === generation) set({ busy: false }); }
  },
}));

useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id) {
    generation += 1;
    useScenarioDraftStore.setState(empty);
  }
});
