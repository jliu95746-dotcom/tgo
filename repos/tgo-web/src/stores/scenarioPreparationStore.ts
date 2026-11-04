import { create } from 'zustand';
import { BusinessScenariosApi } from '@/services/businessScenariosApi';
import type { ScenarioId, ScenarioModule } from '@/types/businessScenarios';
import type { ScenarioPreparationEntry, ScenarioPreparationKey, ScenarioPreparationModule } from '@/types/scenarioPreparation';
import { BUSINESS_SCENARIOS } from '@/utils/businessScenarios';
import { isCurrentPreparationReport, suggestScenarioBindings } from '@/utils/scenarioPreparation';
import { useAuthStore } from './authStore';
import { useScenarioResourceStore } from './scenarioResourceStore';

interface ScenarioPreparationState {
  entries: Partial<Record<ScenarioPreparationKey, ScenarioPreparationEntry>>;
  modules: Partial<Record<ScenarioModule, ScenarioPreparationModule>>;
  load: (module: ScenarioModule) => Promise<void>;
  current: (module: ScenarioModule, id: ScenarioId) => ScenarioPreparationEntry | null;
}

const generations: Record<ScenarioModule, number> = { agents: 0, workflows: 0, devices: 0 };
const identityKey = () => {
  const user = useAuthStore.getState().user;
  return JSON.stringify([user?.id, user?.project_id, user?.role]);
};

export const useScenarioPreparationStore = create<ScenarioPreparationState>((set, get) => ({
  entries: {}, modules: {},
  current: (module, id) => {
    const resource = useScenarioResourceStore.getState();
    if (resource.loading || resource.failed || !resource.catalog
        || resource.catalog.project_id !== useAuthStore.getState().user?.project_id
        || resource.catalog.channel !== resource.channel) return null;
    return get().entries[`${module}:${id}`] ?? null;
  },
  load: async module => {
    const resource = useScenarioResourceStore.getState();
    const catalog = resource.catalog;
    if (resource.loading || resource.failed || !catalog || !useAuthStore.getState().user
        || catalog.project_id !== useAuthStore.getState().user?.project_id || catalog.channel !== resource.channel
        || get().modules[module]) return;
    const actor = identityKey();
    const generation = ++generations[module];
    const owns = () => generation === generations[module] && actor === identityKey()
      && useScenarioResourceStore.getState().catalog === catalog
      && !useScenarioResourceStore.getState().loading && !useScenarioResourceStore.getState().failed;
    const setModule = (value: ScenarioPreparationModule) => set({ modules: { ...get().modules, [module]: value } });
    setModule({ loading: true, failed: false });
    try {
      const templates = await BusinessScenariosApi.templates(module);
      if (!owns()) return;
      const supported = BUSINESS_SCENARIOS.filter(scene => scene.module === module);
      if (!Array.isArray(templates) || new Set(templates.map(template => template.id)).size !== templates.length
          || !supported.every(scene => templates.some(template => template.id === scene.id))
          || !templates.every(template => template.module === module && Number.isInteger(template.version) && template.version > 0
            && typeof template.available === 'boolean' && Array.isArray(template.required_resources)
            && template.required_resources.every(kind => ['model', 'knowledge', 'tool', 'workflow', 'connection', 'device'].includes(kind))
            && supported.some(scene => scene.id === template.id))) throw new Error('Template scope mismatch');
      await Promise.all(templates.map(async template => {
        const key: ScenarioPreparationKey = `${module}:${template.id}`;
        const put = (entry: ScenarioPreparationEntry) => {
          if (owns()) set({ entries: { ...get().entries, [key]: entry } });
        };
        if (!template.available) { put({ template, status: 'planned', report: null }); return; }
        put({ template, status: 'checking', report: null });
        try {
          const bindings = suggestScenarioBindings(template, catalog);
          const report = await BusinessScenariosApi.readiness({ template_id: template.id, channel: resource.channel, bindings });
          if (!owns()) return;
          if (!isCurrentPreparationReport(report, template, catalog, bindings)) throw new Error('Preparation scope mismatch');
          put({ template, status: 'checked', report });
        } catch { put({ template, status: 'failed', report: null }); }
      }));
      if (owns()) setModule({ loading: false, failed: false });
    } catch { if (owns()) setModule({ loading: false, failed: true }); }
  },
}));

function invalidate() {
  for (const module of ['agents', 'workflows', 'devices'] as const) generations[module] += 1;
  useScenarioPreparationStore.setState({ entries: {}, modules: {} });
}

useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id
      || state.user?.role !== previous.user?.role) invalidate();
});
useScenarioResourceStore.subscribe((state, previous) => {
  if (state.catalog !== previous.catalog || state.channel !== previous.channel
      || state.loading !== previous.loading || state.failed !== previous.failed) invalidate();
});
