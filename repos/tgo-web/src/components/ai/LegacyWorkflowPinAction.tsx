import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import ConfirmDialog from '@/components/ui/ConfirmDialog';
import { AIAgentsApiService } from '@/services/aiAgentsApi';
import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import type { WorkflowReleaseSummary } from '@/types/workflow';
import type {
  WorkflowPinTransitionCommand, WorkflowPinTransitionReceipt,
  WorkflowReleaseAuditItem,
} from '@/types/workflowReleaseAudit';

interface Props {
  item: WorkflowReleaseAuditItem;
  identity: string;
  onChanged: () => void;
}

type Action = 'disable' | 'pin' | null;

function owns(identity: string): boolean {
  const user = useAuthStore.getState().user;
  return user?.role === 'admin' && `${user.project_id}:${user.id}` === identity;
}

export default function LegacyWorkflowPinAction({ item, identity, onChanged }: Props) {
  const { t } = useTranslation();
  const [releases, setReleases] = useState<WorkflowReleaseSummary[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Action>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<WorkflowPinTransitionReceipt | null>(null);
  const [history, setHistory] = useState<WorkflowPinTransitionReceipt | null>(null);

  const loadReleases = async () => {
    if (!owns(identity)) return;
    setBusy(true);
    setError(null);
    try {
      const list = await WorkflowApiService.getReleases(item.workflow_id);
      if (!owns(identity)) return;
      setReleases(list);
      setSelected(null);
      if (list.length === 0) setError(t('workflowRelease.audit.noReleases'));
    } catch {
      if (owns(identity)) setError(t('workflowRelease.audit.versionsFailed'));
    } finally {
      if (owns(identity)) setBusy(false);
    }
  };

  const execute = async () => {
    if (!confirm || !owns(identity)) return;
    if (confirm === 'pin' && (selected === null || !releases?.some(item => item.version === selected))) return;
    setBusy(true);
    setError(null);
    const action = confirm;
    let submittedRequestId: string | null = null;
    setConfirm(null);
    try {
      if (action === 'disable') {
        await AIAgentsApiService.setAgentWorkflowEnabled(item.agent_id, item.workflow_id, false);
      } else {
        const command: WorkflowPinTransitionCommand = {
          request_id: requestId ?? crypto.randomUUID(),
          expected_binding_id: item.binding_id,
          release_version: selected!,
          confirmed_disabled: true,
        };
        submittedRequestId = command.request_id;
        setRequestId(command.request_id);
        const result = await AIAgentsApiService.pinLegacyWorkflow(
          item.agent_id, item.workflow_id, command
        );
        if (owns(identity)) setReceipt(result);
      }
      if (owns(identity)) onChanged();
    } catch {
      if (!owns(identity)) return;
      if (action === 'pin' && submittedRequestId) {
        try {
          const history = await AIAgentsApiService.getWorkflowPinTransitions(item.agent_id);
          const matched = history.data.find(entry => entry.request_id === submittedRequestId);
          if (matched) {
            setReceipt(matched);
            onChanged();
            return;
          }
        } catch {
          // The same request ID is retained for an explicit retry.
        }
      }
      setError(t('workflowRelease.audit.actionFailed'));
    } finally {
      if (owns(identity)) setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {item.workflow_enabled ? (
        <button type="button" disabled={busy} onClick={() => setConfirm('disable')}
          className="rounded-lg border border-amber-300 px-3 py-1.5 text-amber-800 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-800 dark:text-amber-200">
          {t('workflowRelease.audit.disable')}
        </button>
      ) : releases === null ? (
        <button type="button" disabled={busy || !item.details_available} onClick={() => void loadReleases()}
          className="rounded-lg border border-blue-200 px-3 py-1.5 text-blue-700 hover:bg-blue-50 disabled:opacity-50 dark:border-blue-900 dark:text-blue-300">
          {t('workflowRelease.audit.selectRelease')}
        </button>
      ) : (
        <>
          <label className="sr-only" htmlFor={`legacy-release-${item.binding_id}`}>
            {t('workflowRelease.audit.selectRelease')}
          </label>
          <select id={`legacy-release-${item.binding_id}`} value={selected ?? ''} disabled={busy}
            onChange={event => { setSelected(Number(event.target.value) || null); setRequestId(null); }}
            className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100">
            <option value="">{t('workflowRelease.audit.selectRelease')}</option>
            {releases.map(release => <option key={release.id} value={release.version}>v{release.version}</option>)}
          </select>
          <button type="button" disabled={busy || selected === null} onClick={() => setConfirm('pin')}
            className="rounded-lg bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {t('workflowRelease.audit.pin')}
          </button>
        </>
      )}
      {error && <span role="alert" className="text-red-700 dark:text-red-300">{error}</span>}
      <button type="button" disabled={busy} onClick={() => {
        setBusy(true);
        setError(null);
        void AIAgentsApiService.getWorkflowPinTransitions(item.agent_id)
          .then(response => {
            if (owns(identity)) setHistory(response.data.find(
              entry => entry.binding_id === item.binding_id
            ) ?? null);
          })
          .catch(() => { if (owns(identity)) setError(t('workflowRelease.audit.historyFailed')); })
          .finally(() => { if (owns(identity)) setBusy(false); });
      }} className="rounded-lg px-2 py-1.5 text-gray-600 hover:bg-gray-100 disabled:opacity-50 dark:text-gray-300 dark:hover:bg-gray-800">
        {t('workflowRelease.audit.history')}
      </button>
      {history && <span className="text-gray-600 dark:text-gray-300">
        {t('workflowRelease.audit.historyEntry', { version: history.release_version, time: new Date(history.created_at).toLocaleString() })}
      </span>}
      {receipt && <span role="status" className="text-green-700 dark:text-green-300">
        {t('workflowRelease.audit.pinned', { version: receipt.release_version })}
      </span>}
      <ConfirmDialog isOpen={confirm !== null} isLoading={busy}
        title={t(confirm === 'disable' ? 'workflowRelease.audit.disableTitle' : 'workflowRelease.audit.pinTitle')}
        message={t(confirm === 'disable' ? 'workflowRelease.audit.disableConfirm' : 'workflowRelease.audit.pinConfirm', { version: selected })}
        onConfirm={() => void execute()} onCancel={() => setConfirm(null)} />
    </div>
  );
}
