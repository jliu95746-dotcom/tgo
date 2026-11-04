import { create } from 'zustand';
import { workflowScheduleApi } from '@/services/workflowScheduleApi';
import { useAuthStore } from './authStore';
import { workflowEventOwner } from './workflowEventTestStore';
import { createScheduleActions, newScheduleState } from './workflowScheduleActions';
import type { ScheduleUiState } from '@/types/workflowSchedules';
import i18n from '@/i18n';

type ScheduleStore = ScheduleUiState & ReturnType<typeof createScheduleActions>;
export const useWorkflowScheduleStore = create<ScheduleStore>((set, get) => ({
  ...newScheduleState(),
  ...createScheduleActions(set, get, {
    api: workflowScheduleApi, currentOwner: () => workflowEventOwner(useAuthStore.getState().user),
    createId: () => crypto.randomUUID(), translate: key => i18n.t(key),
    storage: { getItem: key => sessionStorage.getItem(key),
      setItem: (key, value) => sessionStorage.setItem(key, value), removeItem: key => sessionStorage.removeItem(key) },
  }),
}));
