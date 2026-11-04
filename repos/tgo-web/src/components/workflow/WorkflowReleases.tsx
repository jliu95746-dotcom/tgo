import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { History, X } from 'lucide-react';
import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import type { WorkflowReleaseSummary } from '@/types/workflow';

export default function WorkflowReleases() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<WorkflowReleaseSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [target, setTarget] = useState<number | null>(null);
  const generation = useRef(0);
  const workflow = useWorkflowStore(state => state.currentWorkflow);
  const user = useAuthStore(state => state.user);
  const workflowId = workflow?.id;
  const publicationRevision = workflow?.publication_revision;
  const owner = user ? `${user.project_id}:${user.id}` : null;
  const dialog = useDialogFocus<HTMLDivElement>(() => { if (!busy) setOpen(false); }, open);
  useEffect(() => { setOpen(false); setHistory([]); setTarget(null); }, [owner, workflowId]);
  useEffect(() => {
    const version = ++generation.current;
    setTarget(null); setHistory([]); setFailed(false); setBusy(false);
    if (open && workflowId && user?.project_id) {
      setBusy(true);
      void WorkflowApiService.getReleases(workflowId).then(rows => {
        if (version === generation.current) setHistory(rows);
      }).catch(() => { if (version === generation.current) setFailed(true); })
        .finally(() => { if (version === generation.current) setBusy(false); });
    }
    return () => { generation.current += 1; };
  }, [open, workflowId, publicationRevision, user?.id, user?.project_id]);
  const rollback = async () => {
    if (!workflowId || target === null || busy || user?.role !== 'admin') return;
    const version = ++generation.current;
    const stillOwned = () => {
      const current = useAuthStore.getState().user;
      return current?.role === 'admin' && `${current.project_id}:${current.id}` === owner;
    };
    setBusy(true); setFailed(false);
    try {
      const result = await WorkflowApiService.rollbackWorkflow(workflowId, { target_version: target, expected_publication_revision: publicationRevision ?? 0 });
      if (version !== generation.current || !stillOwned()) return;
      // Preserve unsaved canvas edits; rollback only changes the production pointer.
      useWorkflowStore.setState(state => state.currentWorkflow?.id === workflowId ? {
        currentWorkflow: { ...state.currentWorkflow, status: result.status, published_version: result.published_version, publication_revision: result.publication_revision },
      } : {});
      void useWorkflowStore.getState().loadWorkflows();
      setTarget(null);
    } catch { if (version === generation.current) setFailed(true); }
    finally { if (version === generation.current) setBusy(false); }
  };
  if (!workflow) return null;
  return <>
    <button type="button" onClick={() => setOpen(true)} title={t('businessScenarios.workflow.releases.title')} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><History size={18} /></button>
    {open && <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="workflow-release-title" className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/50 p-4">
      <section className="max-h-[80vh] w-full max-w-xl overflow-y-auto rounded-3xl bg-white p-6 shadow-xl dark:bg-gray-900">
        <div className="flex items-center justify-between"><h2 id="workflow-release-title" className="font-bold text-gray-900 dark:text-gray-100">{t('businessScenarios.workflow.releases.title')}</h2><button type="button" disabled={busy} aria-label={t('common.close')} onClick={() => setOpen(false)} className="text-gray-500"><X size={20} /></button></div>
        <p className="mt-3 text-xs leading-6 text-gray-500 dark:text-gray-400">{t('businessScenarios.workflow.releases.hint')}</p>
        <p className="mt-2 text-sm text-blue-600 dark:text-blue-300">{workflow.published_version ? t('businessScenarios.workflow.releases.current', { version: workflow.published_version }) : t('businessScenarios.workflow.releases.unpublished')}</p>
        {busy && <p role="status" className="mt-3 text-sm text-gray-500">{t('common.loading')}</p>}
        {failed && <p role="alert" className="mt-3 text-sm text-red-600">{t('businessScenarios.workflow.releases.failed')}</p>}
        {!busy && !failed && history.length === 0 && <p className="mt-4 text-sm text-gray-500">{t('businessScenarios.workflow.releases.empty')}</p>}
        <ul className="mt-4 space-y-3">{history.map(release => <li key={release.id} className="flex items-center justify-between gap-4 rounded-2xl border border-gray-100 p-4 dark:border-gray-800">
          <div><p className="text-sm font-medium text-gray-800 dark:text-gray-100">{t('businessScenarios.workflow.releases.version', { version: release.version, draft: release.draft_version })}</p><time className="mt-1 text-xs text-gray-500" dateTime={release.created_at}>{new Date(release.created_at).toLocaleString()}</time></div>
          <button type="button" disabled={busy || user?.role !== 'admin' || release.version === workflow.published_version} onClick={() => setTarget(release.version)} className="shrink-0 text-xs text-blue-600 disabled:opacity-40">{t('businessScenarios.workflow.releases.restore')}</button>
        </li>)}</ul>
        {target !== null && <div role="alert" className="mt-4 rounded-2xl bg-amber-50 p-4 dark:bg-amber-900/20">
          <p className="text-sm text-amber-800 dark:text-amber-200">{t('businessScenarios.workflow.releases.confirm', { version: target })}</p>
          <div className="mt-3 flex justify-end gap-3"><button type="button" disabled={busy} onClick={() => setTarget(null)} className="text-sm text-gray-500">{t('common.cancel')}</button><button type="button" disabled={busy} onClick={() => void rollback()} className="rounded-xl bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-40">{t('businessScenarios.workflow.releases.restore')}</button></div>
        </div>}
      </section>
    </div>}
  </>;
}
