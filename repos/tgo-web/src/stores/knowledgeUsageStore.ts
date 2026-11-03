import { create } from 'zustand';
import { KnowledgeGovernanceApiService } from '@/services/knowledgeGovernanceApi';
import type { KnowledgeGovernanceRecord } from '@/types';

interface KnowledgeUsageEntry {
  records: KnowledgeGovernanceRecord[];
  loading: boolean;
  loaded: boolean;
  error: string | null;
}

interface KnowledgeUsageStore {
  entries: Record<string, KnowledgeUsageEntry>;
  load: (collectionId: string) => Promise<void>;
}

const requests = new Map<string, Promise<void>>();
export const useKnowledgeUsageStore = create<KnowledgeUsageStore>((set) => ({
  entries: {},
  load: (collectionId) => {
    const pending = requests.get(collectionId);
    if (pending) return pending;
    set((state) => ({ entries: { ...state.entries, [collectionId]: { records: state.entries[collectionId]?.records || [], loading: true, loaded: false, error: null } } }));
    const task = KnowledgeGovernanceApiService.list(collectionId).then((response) => {
      set((state) => ({ entries: { ...state.entries, [collectionId]: { records: response.data, loading: false, loaded: true, error: null } } }));
    }).catch((error: unknown) => {
      set((state) => ({ entries: { ...state.entries, [collectionId]: { records: [], loading: false, loaded: false, error: error instanceof Error ? error.message : 'KNOWLEDGE_STATUS_LOAD_FAILED' } } }));
    }).finally(() => { requests.delete(collectionId); });
    requests.set(collectionId, task);
    return task;
  },
}));
