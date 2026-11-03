import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentDeviceScriptCasesApi } from '@/services/agentDeviceScriptCasesApi';
import { useAuthStore } from '@/stores/authStore';
import { validScriptEvidence } from '@/utils/agentDeviceScriptCaseValidation';
import type { AgentScriptEvidence } from '@/types/agentDeviceScriptCases';
import type { AgentScriptTrialReport, ScriptBusinessValues } from '@/types/agentDeviceScripts';

export function ScriptValues({ values }: { values: ScriptBusinessValues }) {
  return <dl className="space-y-2 text-sm">{Object.entries(values).map(([field, value]) =>
    <div key={field} className="min-w-0"><dt className="break-all font-medium text-gray-600 dark:text-gray-300">{field}</dt>
      <dd className="whitespace-pre-wrap break-words text-gray-900 dark:text-white">{String(value)}</dd></div>)}</dl>;
}

export default function AgentDeviceScriptEvidence({ agentId, report }: { agentId: string; report: AgentScriptTrialReport }) {
  const { t } = useTranslation(), user = useAuthStore(state => state.user);
  // Private result bodies deliberately remain inside this mounted view.
  const [value, setValue] = useState<AgentScriptEvidence | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    setValue(null); setBusy(false); setError(false);
    return () => operation.current?.abort();
  }, [agentId, report.id, user?.id, user?.project_id]);
  const read = async () => {
    if (!user || busy) return;
    operation.current?.abort(); const controller = new AbortController(); operation.current = controller;
    setValue(null); setError(false); setBusy(true);
    try {
      const body = await AgentDeviceScriptCasesApi.evidence(agentId, report.id, controller.signal);
      const current = useAuthStore.getState().user;
      if (controller.signal.aborted || current?.id !== user.id || current?.project_id !== user.project_id) return;
      if (!validScriptEvidence(body, user.project_id, user.id, agentId, report.id)) throw Error('invalidResponse');
      setValue(body);
    } catch { if (!controller.signal.aborted) setError(true); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const text = (name: string) => t(`agentDeviceScripts.cases.${name}`);
  return <div className="space-y-3 border-t border-gray-200 pt-3 dark:border-gray-700">
    <div className="flex flex-wrap gap-4">
      <button type="button" disabled={busy} onClick={() => void read()} className="text-sm font-medium text-blue-600 disabled:opacity-40">{text(busy ? 'reading' : 'evidence')}</button>
      {value && <button type="button" onClick={() => setValue(null)} className="text-sm text-gray-500">{text('hide')}</button>}
    </div>
    {error && <p role="alert" className="text-sm text-red-600">{text('readError')}</p>}
    {value && <section aria-label={text('evidence')} className="space-y-3">
      <p role="status" className="text-sm text-gray-600 dark:text-gray-300">{text(`evidenceStatus.${value.status}`)}</p>
      {value.case && <p className="break-words text-sm font-medium text-gray-900 dark:text-white">{value.case.title} · {t('agentDeviceScripts.cases.version', { revision: value.case.revision })}</p>}
      {value.status === 'available' && value.values && value.result_fields && <>
        <h5 className="text-sm font-medium text-gray-700 dark:text-gray-200">{text('savedInputs')}</h5><ScriptValues values={value.values.parameters} />
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <div className="min-w-0"><h5 className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-200">{text('savedExpected')}</h5><ScriptValues values={value.values.expected_fields} /></div>
          <div className="min-w-0"><h5 className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-200">{text('actual')}</h5><ScriptValues values={value.result_fields} /></div>
        </div>
      </>}
      {value.expires_at && <p className="break-words text-xs text-gray-500">{t('agentDeviceScripts.cases.expires', { date: new Date(value.expires_at).toLocaleString() })}</p>}
      <p className="text-xs leading-6 text-gray-500">{text('notApproved')}</p>
    </section>}
  </div>;
}
