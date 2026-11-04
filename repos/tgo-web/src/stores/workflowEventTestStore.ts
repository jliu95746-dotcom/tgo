import { create } from 'zustand';
import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import type { WorkflowEventTestState } from '@/types/workflowEvents';
import { createWorkflowEventTestActions } from './workflowEventTestActions';
import i18n from '@/i18n';

export function workflowEventOwner(user: { id: string; project_id: string; role: string } | null): string | null {
  return user?.role === 'admin' ? `${user.project_id}:${user.id}:admin` : null;
}

type WorkflowEventTestStore = WorkflowEventTestState & ReturnType<typeof createWorkflowEventTestActions>;

export const useWorkflowEventTestStore = create<WorkflowEventTestStore>((set, get) => ({
  ownerKey: null, activeWorkflowId: null, sessions: {},
  ...createWorkflowEventTestActions(set, get, {
    api: WorkflowApiService,
    currentOwner: () => workflowEventOwner(useAuthStore.getState().user),
    createId: () => crypto.randomUUID(),
    translate: key => i18n.t(key),
  }),
}));
