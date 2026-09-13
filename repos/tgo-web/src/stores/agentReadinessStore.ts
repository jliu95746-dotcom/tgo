import { create } from 'zustand';
import AIProvidersApiService from '@/services/aiProvidersApi';
import type { AgentModelConfiguration } from '@/utils/agentReadiness';

interface AgentReadinessState {
  projectId: string | null;
  models: AgentModelConfiguration[];
  status: 'idle' | 'loading' | 'loaded' | 'error';
  requestId: number;
  load: (projectId: string, force?: boolean) => Promise<void>;
}

export const useAgentReadinessStore = create<AgentReadinessState>((set, get) => ({
  projectId: null,
  models: [],
  status: 'idle',
  requestId: 0,
  load: async (projectId, force = false) => {
    const current = get();
    if (current.projectId === projectId && (current.status === 'loading'
      || (!force && current.status === 'loaded'))) return;
    const requestId = current.requestId + 1;
    set({ projectId, requestId, models: [], status: 'loading' });
    try {
      const service = new AIProvidersApiService();
      const models: AgentModelConfiguration[] = [];
      let offset = 0;
      while (true) {
        const response = await service.listProjectModels({ model_type: 'chat', is_active: true, limit: 100, offset });
        if (get().requestId !== requestId) return;
        models.push(...response.data.map(({ provider_id, model_id, model_type, is_active }) =>
          ({ provider_id, model_id, model_type, is_active })));
        if (!response.pagination.has_next) break;
        if (response.data.length === 0) throw new Error('Incomplete model pagination');
        offset += response.data.length;
      }
      set({ models, status: 'loaded' });
    } catch {
      if (get().requestId === requestId) set({ models: [], status: 'error' });
    }
  },
}));
