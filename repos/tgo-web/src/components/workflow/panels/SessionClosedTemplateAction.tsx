import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { workflowEventOwner } from '@/stores/workflowEventTestStore';
import { WorkflowApiService } from '@/services/workflowApi';
import { buildSessionClosedWorkflow } from '@/utils/sessionClosedWorkflowTemplate';
import { migrateNodes } from '@/utils/workflowTransforms';

export default function SessionClosedTemplateAction() {
  const { t } = useTranslation();
  const ownerKey = useAuthStore(state => workflowEventOwner(state.user));
  const workflowId = useWorkflowStore(state => state.currentWorkflow?.id);
  const dirty = useWorkflowStore(state => state.isDirty);
  const [busy, setBusy] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const epoch = useRef(0);
  useEffect(() => {
    epoch.current += 1; setBusy(false); setCreatedId(null); setError('');
    return () => { epoch.current += 1; };
  }, [ownerKey, workflowId]);
  if (!ownerKey) return null;
  const valid = (generation: number) => generation === epoch.current
    && ownerKey === workflowEventOwner(useAuthStore.getState().user)
    && workflowId === useWorkflowStore.getState().currentWorkflow?.id;
  const createDraft = async () => {
    if (busy || createdId) return;
    const generation = epoch.current;
    setBusy(true); setError('');
    try {
      const result = await WorkflowApiService.createWorkflow(buildSessionClosedWorkflow(t('workflowEvents.sessionTemplate.name')));
      if (valid(generation)) setCreatedId(result.id);
    } catch (failure: unknown) {
      if (valid(generation)) setError(failure instanceof Error ? failure.message : t('workflowEvents.sessionTemplate.createFailed'));
    } finally { if (valid(generation)) setBusy(false); }
  };
  const openDraft = async () => {
    if (!createdId || busy || useWorkflowStore.getState().isDirty || !valid(epoch.current)) return;
    const generation = epoch.current;
    setBusy(true); setError('');
    try {
      const result = await WorkflowApiService.getWorkflow(createdId);
      if (!valid(generation) || useWorkflowStore.getState().isDirty) return;
      if (result.id !== createdId) throw new Error(t('workflowEvents.sessionTemplate.openFailed'));
      const nodes = migrateNodes(result.definition.nodes);
      result.definition.nodes = nodes;
      useWorkflowStore.getState().clearExecution();
      useWorkflowStore.getState().setCurrentWorkflow(result);
      useWorkflowStore.setState({ isLoadingCurrentWorkflow: false, currentWorkflowError: null });
    } catch (failure: unknown) {
      if (valid(generation)) setError(failure instanceof Error ? failure.message : t('workflowEvents.sessionTemplate.openFailed'));
    } finally { if (valid(generation)) setBusy(false); }
  };
  return <div className="space-y-2 rounded-xl border border-purple-100 p-3 dark:border-purple-900/50">
    <p className="text-xs leading-5 text-gray-500">{t('workflowEvents.sessionTemplate.hint')}</p>
    {!createdId ? <button type="button" disabled={busy} onClick={() => void createDraft()} className="text-xs font-semibold text-purple-600 disabled:opacity-40 dark:text-purple-400">{t(busy ? 'workflowEvents.sessionTemplate.creating' : 'workflowEvents.sessionTemplate.create')}</button>
      : <><p role="status" className="text-xs text-green-600 dark:text-green-400">{t('workflowEvents.sessionTemplate.created')}</p>
        <button type="button" disabled={dirty || busy} onClick={() => void openDraft()} className="text-xs font-semibold text-purple-600 disabled:opacity-40 dark:text-purple-400">{t('workflowEvents.sessionTemplate.open')}</button>
        {dirty && <p className="text-xs leading-5 text-amber-600 dark:text-amber-300">{t('workflowEvents.sessionTemplate.saveFirst')}</p>}</>}
    {error && <p role="alert" className="text-xs text-red-600 dark:text-red-400">{error}</p>}
  </div>;
}
