import { create } from 'zustand';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import type {
  MessageAnalysisViewState,
  StaffMessageAnalysisLookup,
} from '@/types/messageAnalysis';


const MAX_BATCH_SIZE = 100;
const inFlightKeys = new Set<string>();

export const buildMessageAnalysisCacheKey = (
  lookup: StaffMessageAnalysisLookup,
): string => `${lookup.channel_id}\u0000${lookup.source_message_id}`;

const buildInFlightKey = (
  projectId: string,
  lookup: StaffMessageAnalysisLookup,
): string => `${projectId}\u0000${buildMessageAnalysisCacheKey(lookup)}`;

interface MessageAnalysisState {
  scopeProjectId: string | null;
  analysesByMessage: Record<string, MessageAnalysisViewState>;
  loadMessageAnalyses: (
    projectId: string,
    lookups: readonly StaffMessageAnalysisLookup[],
  ) => Promise<void>;
  clearMessageAnalyses: () => void;
}

const uniquePendingLookups = (
  projectId: string,
  lookups: readonly StaffMessageAnalysisLookup[],
  cached: Record<string, MessageAnalysisViewState>,
): StaffMessageAnalysisLookup[] => {
  const seen = new Set<string>();
  return lookups.filter((lookup) => {
    const key = buildMessageAnalysisCacheKey(lookup);
    if (seen.has(key) || inFlightKeys.has(buildInFlightKey(projectId, lookup))) {
      return false;
    }
    seen.add(key);
    const state = cached[key];
    return state === undefined || state.status === 'error';
  });
};

export const useMessageAnalysisStore = create<MessageAnalysisState>()(
  (set, get) => ({
    scopeProjectId: null,
    analysesByMessage: {},

    loadMessageAnalyses: async (projectId, lookups) => {
      if (!projectId || lookups.length === 0) return;

      const current = get();
      const scopeChanged = current.scopeProjectId !== projectId;
      const cached = scopeChanged ? {} : current.analysesByMessage;
      const pending = uniquePendingLookups(projectId, lookups, cached);

      if (scopeChanged) {
        inFlightKeys.clear();
        set({ scopeProjectId: projectId, analysesByMessage: {} });
      }
      if (pending.length === 0) return;

      const loadingStates = { ...cached };
      pending.forEach((lookup) => {
        const key = buildMessageAnalysisCacheKey(lookup);
        inFlightKeys.add(buildInFlightKey(projectId, lookup));
        loadingStates[key] = { status: 'loading' };
      });
      set({ scopeProjectId: projectId, analysesByMessage: loadingStates });

      for (let offset = 0; offset < pending.length; offset += MAX_BATCH_SIZE) {
        const batch = pending.slice(offset, offset + MAX_BATCH_SIZE);
        try {
          const response = await messageAnalysisApiService.queryStaffMessageAnalyses({
            messages: batch,
          });
          const availableByKey = new Map(
            response.items.map((item) => [
              buildMessageAnalysisCacheKey(item),
              item,
            ]),
          );
          set((state) => {
            if (state.scopeProjectId !== projectId) return state;
            const next = { ...state.analysesByMessage };
            batch.forEach((lookup) => {
              const key = buildMessageAnalysisCacheKey(lookup);
              const analysis = availableByKey.get(key);
              next[key] = analysis === undefined
                ? { status: 'not_found' }
                : { status: 'available', analysis };
            });
            return { analysesByMessage: next };
          });
        } catch {
          set((state) => {
            if (state.scopeProjectId !== projectId) return state;
            const next = { ...state.analysesByMessage };
            batch.forEach((lookup) => {
              next[buildMessageAnalysisCacheKey(lookup)] = { status: 'error' };
            });
            return { analysesByMessage: next };
          });
        } finally {
          batch.forEach((lookup) => {
            inFlightKeys.delete(buildInFlightKey(projectId, lookup));
          });
        }
      }
    },

    clearMessageAnalyses: () => {
      inFlightKeys.clear();
      set({ scopeProjectId: null, analysesByMessage: {} });
    },
  }),
);
