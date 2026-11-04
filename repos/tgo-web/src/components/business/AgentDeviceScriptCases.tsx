import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentDeviceScriptCasesApi } from '@/services/agentDeviceScriptCasesApi';
import { useAgentDeviceScriptCasesStore } from '@/stores/agentDeviceScriptCasesStore';
import { useAgentDeviceScriptStore } from '@/stores/agentDeviceScriptStore';
import { useAuthStore } from '@/stores/authStore';
import { validScriptCaseRead } from '@/utils/agentDeviceScriptCaseValidation';
import type { AgentScriptCaseRead } from '@/types/agentDeviceScriptCases';
import { ScriptValues } from './AgentDeviceScriptEvidence';

export default function AgentDeviceScriptCases({ agentId, disabled, onEdit }: {
  agentId: string; disabled: boolean; onEdit: (body: AgentScriptCaseRead) => void;
}) {
  const { t } = useTranslation(), state = useAgentDeviceScriptCasesStore(), user = useAuthStore(value => value.user);
  const [body, setBody] = useState<AgentScriptCaseRead | null>(null), [reading, setReading] = useState(false), [error, setError] = useState(false);
  const request = useRef<AbortController | null>(null);
  const text = (name: string) => t(`agentDeviceScripts.cases.${name}`);
  const latest = body ? state.items.find(item => item.case_id === body.case.case_id) : null;
  const editable = Boolean(body && latest?.id === body.case.id && latest.revision === body.case.revision
    && !['expired', 'superseded'].includes(latest.status) && Date.parse(body.case.expires_at) > Date.now());
  useEffect(() => {
    setBody(null); setError(false); setReading(false);
    return () => request.current?.abort();
  }, [agentId, user?.id, user?.project_id]);
  const read = async (id: string) => {
    if (!user || reading) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setBody(null); setReading(true); setError(false);
    try {
      const value = await AgentDeviceScriptCasesApi.read(agentId, id, controller.signal), current = useAuthStore.getState().user;
      if (controller.signal.aborted || current?.id !== user.id || current?.project_id !== user.project_id) return;
      if (!validScriptCaseRead(value, user.project_id, agentId, id)) throw Error('invalidResponse');
      setBody(value);
    } catch { if (!controller.signal.aborted) setError(true); }
    finally { if (!controller.signal.aborted) setReading(false); }
  };
  return <section aria-label={text('title')} className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
    <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-medium text-gray-900 dark:text-white">{text('title')}</h4>
      <button type="button" disabled={state.loading || state.busy} onClick={() => void state.refresh()} className="text-sm text-blue-600 disabled:opacity-40">{text('refresh')}</button></div>
    {state.error && <p role="alert" className="text-sm text-red-600">{t(`agentDeviceScripts.errors.${state.error}`)}</p>}
    {state.pending && <div role="status" className="space-y-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
      <p>{text('saveUnknown')}</p><p className="break-all text-xs">{state.pending.requestId}</p>
      <button type="button" disabled={state.busy} onClick={() => void state.recover()} className="text-blue-600 disabled:opacity-40">{text('recover')}</button>
      <button type="button" disabled={state.cancelling || !state.storageReady} onClick={() => void state.cancel()} className="ml-4 text-red-600 disabled:opacity-40">{text(state.cancelling ? 'cancellingSave' : 'cancelSave')}</button>
      <p className="text-xs leading-6">{text('cancelSaveHint')}</p>
    </div>}
    {state.saveState && state.saveState.status !== 'saved' && <p role="status" className="text-sm text-gray-600 dark:text-gray-300">{text(`saveStatus.${state.saveState.status}`)}</p>}
    {state.lastSaved && <p role="status" className="text-sm text-green-700 dark:text-green-300">{t('agentDeviceScripts.cases.saved', { revision: state.lastSaved.revision })}</p>}
    {!state.items.length && !state.loading && <p className="text-sm text-gray-500">{text('empty')}</p>}
    {state.items.map(item => <article key={item.id} className="space-y-2 rounded-xl bg-gray-50 p-3 dark:bg-gray-800/40">
      <p className="break-words font-medium text-gray-900 dark:text-white">{item.title}</p>
      <p className="text-xs text-gray-500">{t('agentDeviceScripts.cases.version', { revision: item.revision })} · {text(`status.${item.status}`)}</p>
      <div className="flex flex-wrap gap-4">
        <button type="button" disabled={reading || item.status === 'expired'} onClick={() => void read(item.id)} className="text-sm text-blue-600 disabled:opacity-40">{text('view')}</button>
        <button type="button" disabled={disabled || item.status !== 'current' || Date.parse(item.expires_at) <= Date.now()} onClick={() => void useAgentDeviceScriptStore.getState().startCase(item)} className="text-sm font-medium text-blue-600 disabled:opacity-40">{text('run')}</button>
      </div>
    </article>)}
    {state.hasMore && <button type="button" disabled={state.loading || state.busy} onClick={() => void state.refresh(true)} className="text-sm text-blue-600 disabled:opacity-40">{text('more')}</button>}
    {reading && <p role="status" className="text-sm text-gray-500">{text('reading')}</p>}
    {error && <p role="alert" className="text-sm text-red-600">{text('readError')}</p>}
    {body && <div className="space-y-3 border-t border-gray-200 pt-3 dark:border-gray-700">
      <p className="break-words font-medium text-gray-900 dark:text-white">{body.case.title} · {t('agentDeviceScripts.cases.version', { revision: body.case.revision })}</p>
      {latest && latest.id !== body.case.id && <p role="status" className="text-xs text-amber-700 dark:text-amber-300">{text('status.superseded')}</p>}
      <h5 className="text-sm text-gray-500">{text('savedInputs')}</h5><ScriptValues values={body.values.parameters} />
      <h5 className="text-sm text-gray-500">{text('savedExpected')}</h5><ScriptValues values={body.values.expected_fields} />
      <p className="break-words text-xs text-gray-500">{t('agentDeviceScripts.cases.expires', { date: new Date(body.case.expires_at).toLocaleString() })}</p>
      <div className="flex flex-wrap gap-4"><button type="button" disabled={disabled || !editable} onClick={() => onEdit(body)} className="text-sm text-blue-600 disabled:opacity-40">{text('edit')}</button>
        <button type="button" onClick={() => setBody(null)} className="text-sm text-gray-500">{text('hide')}</button></div>
    </div>}
  </section>;
}
