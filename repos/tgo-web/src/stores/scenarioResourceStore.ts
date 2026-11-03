import { create } from 'zustand';
import { BusinessScenariosApi } from '@/services/businessScenariosApi';
import type { KnowledgeChannel, ScenarioResourceCatalog } from '@/types/scenarioResources';
import { useAuthStore } from './authStore';

interface ResourceCatalogState {
  catalog: ScenarioResourceCatalog | null;
  channel: KnowledgeChannel;
  loading: boolean;
  failed: boolean;
  load: (refresh?: boolean) => Promise<void>;
  setChannel: (channel: KnowledgeChannel) => void;
}

let requestVersion = 0;
export const useScenarioResourceStore = create<ResourceCatalogState>((set, get) => ({
  catalog: null, channel: 'web', loading: false, failed: false,
  setChannel: channel => {
    requestVersion += 1;
    set({ channel, catalog: null, loading: false, failed: false });
  },
  load: async (refresh = false) => {
    const identity = useAuthStore.getState().user;
    const project = identity?.project_id;
    const channel = get().channel;
    if (!project || !identity || get().loading) return;
    if (!refresh && get().catalog?.project_id === project && get().catalog?.channel === channel) return;
    const version = ++requestVersion;
    set({ loading: true, failed: false });
    try {
      const catalog = await BusinessScenariosApi.resources(channel);
      const current = useAuthStore.getState().user;
      if (version !== requestVersion || current?.id !== identity.id || current.project_id !== project || current.role !== identity.role) return;
      if (catalog.project_id !== project || catalog.channel !== channel) throw new Error('Resource catalogue scope mismatch');
      set({ catalog, loading: false });
    } catch {
      if (version === requestVersion) set({ catalog: null, failed: true, loading: false });
    }
  },
}));

useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id || state.user?.role !== previous.user?.role) {
    requestVersion += 1;
    useScenarioResourceStore.setState({ catalog: null, loading: false, failed: false, channel: 'web' });
  }
});
