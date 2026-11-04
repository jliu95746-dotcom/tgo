import { create } from 'zustand';
import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import { workflowEventOwner } from './workflowEventTestStore';
import { createWorkflowEventSubscriptionActions, newEventSubscriptionState } from './workflowEventSubscriptionActions';
import type { WorkflowEventSubscriptionState } from '@/types/workflowEvents';
import i18n from '@/i18n';

type SubscriptionStore = WorkflowEventSubscriptionState & ReturnType<typeof createWorkflowEventSubscriptionActions>;

export const useWorkflowEventSubscriptionStore = create<SubscriptionStore>((set, get) => ({
  ...newEventSubscriptionState(),
  ...createWorkflowEventSubscriptionActions(set, get, {
    api: WorkflowApiService,
    currentOwner: () => workflowEventOwner(useAuthStore.getState().user),
    translate: key => i18n.t(key),
  }),
}));
