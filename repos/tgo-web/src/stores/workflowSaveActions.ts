import type { Workflow, WorkflowUpdateRequest } from '@/types/workflow';
import { WorkflowApiService } from '@/services/workflowApi';
import i18n from '@/i18n';
import { useAuthStore } from './authStore';

interface SaveState {
  currentWorkflow: Workflow | null;
  refreshWorkflows: () => Promise<void>;
}

interface SavedState {
  currentWorkflow: Workflow;
  isDirty: boolean;
}

function ownerIdentity(): string | null {
  const user = useAuthStore.getState().user;
  return user?.project_id ? JSON.stringify([user.id, user.project_id, user.role]) : null;
}

function payload(workflow: Workflow): WorkflowUpdateRequest {
  return {
    name: workflow.name, description: workflow.description, tags: workflow.tags,
    nodes: workflow.definition.nodes, edges: workflow.definition.edges,
    expected_version: workflow.version,
  };
}

export function createWorkflowSaveAction(
  get: () => SaveState,
  apply: (state: SavedState) => void,
  editorRevision: () => number,
): () => Promise<void> {
  let pending: { key: string; promise: Promise<void> } | null = null;
  return () => {
    const workflow = get().currentWorkflow;
    if (!workflow) return Promise.resolve();
    const owner = ownerIdentity();
    if (!owner) return Promise.reject(new Error(i18n.t('businessScenarios.wizard.identityChanged')));
    const revision = editorRevision();
    const key = JSON.stringify([owner, workflow.id, revision]);
    if (pending?.key === key) return pending.promise;
    const request = payload(workflow);
    const submitted = JSON.stringify(request);
    const run = async () => {
      const updated = await WorkflowApiService.updateWorkflow(workflow.id, request);
      const latest = get().currentWorkflow;
      if (ownerIdentity() !== owner || editorRevision() !== revision || latest?.id !== workflow.id) {
        throw new Error(i18n.t('businessScenarios.wizard.identityChanged'));
      }
      if (updated.id !== workflow.id || updated.version !== workflow.version + 1) {
        throw new Error(i18n.t('businessScenarios.workflow.invalidSaveReceipt'));
      }
      const edited = JSON.stringify(payload(latest)) !== submitted;
      apply({
        currentWorkflow: edited ? {
          ...updated, name: latest.name, description: latest.description,
          tags: latest.tags, definition: latest.definition,
        } : updated,
        isDirty: edited,
      });
      // A roster refresh failure does not invalidate the committed draft.
      try { await get().refreshWorkflows(); } catch { /* The list retains its retry entry. */ }
    };
    const record = { key, promise: Promise.resolve() };
    record.promise = run().finally(() => { if (pending === record) pending = null; });
    pending = record;
    return record.promise;
  };
}
