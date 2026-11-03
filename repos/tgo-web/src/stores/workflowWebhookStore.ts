import { create } from 'zustand';
import { workflowWebhookApi } from '@/services/workflowWebhookApi';
import { useAuthStore } from './authStore';
import { useWorkflowStore } from './workflowStore';
import { workflowEventOwner } from './workflowEventTestStore';
import { createWebhookActions, newWebhookState } from './workflowWebhookActions';
import type { WebhookUiState } from '@/types/workflowWebhooks';
import i18n from '@/i18n';

type WebhookStore = WebhookUiState & ReturnType<typeof createWebhookActions>;
export const useWorkflowWebhookStore = create<WebhookStore>((set, get) => ({
  ...newWebhookState(),
  ...createWebhookActions(set, get, {
    api: workflowWebhookApi, currentOwner: () => workflowEventOwner(useAuthStore.getState().user),
    createId: () => crypto.randomUUID(), translate: key => i18n.t(key),
    canSave: () => useWorkflowStore.getState().currentWorkflow?.id === get().workflowId
      && !useWorkflowStore.getState().isDirty && !useWorkflowStore.getState().isExecuting,
    storage: { getItem: key => sessionStorage.getItem(key),
      setItem: (key, value) => sessionStorage.setItem(key, value), removeItem: key => sessionStorage.removeItem(key) },
  }),
}));
useAuthStore.subscribe((state, previous) => {
  if (workflowEventOwner(state.user) !== workflowEventOwner(previous.user)) useWorkflowWebhookStore.getState().reset();
});
