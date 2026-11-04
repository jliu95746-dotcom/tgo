import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useDeviceScriptStore } from '@/stores/deviceScriptStore';
import { prepareScriptPolicy, type ScriptPolicyForm } from '@/utils/deviceScriptPolicy';
import type { Device } from '@/types/deviceControl';

interface Props { device: Device; draftSaved: boolean }
const initialForm: ScriptPolicyForm = { scriptId: '', timeoutSeconds: 10, maxOutputBytes: 4096, maxRuns: 1 };

export default function DeviceScriptPanel({ device, draftSaved }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const state = useDeviceScriptStore();
  const { open, close } = state;
  const [form, setForm] = useState(initialForm);
  const [confirmation, setConfirmation] = useState<{ revision: number; name: string } | null>(null);
  useEffect(() => {
    const session = open(device);
    return () => { if (session !== null) close(session); };
  }, [device, user?.id, user?.project_id, user?.role, open, close]);
  useEffect(() => {
    const selected = state.catalog?.scripts.find(item => item.effect === 'read_only'
      && item.script_id === state.policy?.policy.script_id)
      ?? state.catalog?.scripts.find(item => item.effect === 'read_only');
    const scope = state.policy?.status !== 'revoked' ? state.policy?.policy : null;
    setForm(selected ? {
      scriptId: selected.script_id,
      timeoutSeconds: Math.min(scope?.timeout_seconds ?? 10, selected.timeout_seconds),
      maxOutputBytes: Math.min(scope?.max_output_bytes ?? 4096, selected.max_output_bytes),
      maxRuns: Math.min(scope?.max_runs ?? 1, selected.max_runs),
    } : initialForm);
    setConfirmation(null);
  }, [state.owner?.session, state.catalog, state.policy]);

  const allowed = user?.role === 'admin' && user.project_id === device.project_id;
  const own = allowed && state.owner?.deviceId === device.id
    && state.owner.projectId === device.project_id && state.owner.userId === user.id;
  const selected = state.catalog?.scripts.find(item => item.script_id === form.scriptId);
  const waiting = !own || state.loading || state.busy || !state.loaded || state.error;
  const payload = own ? prepareScriptPolicy({ ...form, ...state, deviceId: device.id, projectId: device.project_id }) : null;
  const otherMode = [state.file, state.screen].some(value => value && value.status !== 'revoked');
  const active = state.policy !== null && state.policy.status !== 'revoked';
  const currentName = state.catalog?.scripts.find(item => item.script_id === state.policy?.policy.script_id
    && item.grant_digest === state.policy?.policy.grant_digest)?.title ?? state.policy?.policy.script_id ?? '';
  const choose = (scriptId: string) => {
    const item = state.catalog?.scripts.find(value => value.script_id === scriptId);
    if (!item) return;
    setForm({ scriptId, timeoutSeconds: Math.min(10, item.timeout_seconds), maxOutputBytes: Math.min(4096, item.max_output_bytes), maxRuns: 1 });
  };
  const inputClass = 'mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800';
  return <section className="mt-5 space-y-4 rounded-2xl border border-gray-200 p-4 dark:border-gray-700" aria-labelledby="device-script-title">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4 id="device-script-title" className="font-semibold text-gray-900 dark:text-white">{t('deviceScripts.title')}</h4>
      <button type="button" disabled={state.loading || state.busy || !allowed} onClick={() => void state.refresh()}
        className="flex items-center gap-2 rounded-xl px-2 py-1 text-sm text-blue-600 disabled:opacity-50"><RefreshCw size={15} />{t('deviceScripts.refresh')}</button>
    </div>
    <p className="text-xs leading-6 text-gray-500">{t('deviceScripts.help')}</p>
    {!allowed ? <p role="alert" className="text-sm text-amber-600">{t('deviceScripts.admin')}</p> : <>
      {state.loading && <p role="status" className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" />{t('deviceScripts.loading')}</p>}
      {state.error && <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{t('deviceScripts.error')}</p>}
      {state.notice && <p role="status" className="text-sm text-blue-600">{t(`deviceScripts.${state.notice}`)}</p>}
      {own && <>
        <p role="status" className="text-sm text-gray-600 dark:text-gray-300">{t(`deviceScripts.status.${state.policy?.status ?? 'none'}`)}</p>
        {state.policy && <p className="break-words text-xs text-gray-500">{t('deviceScripts.currentPolicy', { name: currentName, revision: state.policy.policy.revision })}</p>}
        {otherMode && <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">{t('deviceScripts.conflict')}</p>}
        {state.loaded && (!state.local || ['disabled', 'unavailable', 'closed'].includes(state.local.session.state)) && <p className="text-sm leading-6 text-gray-500">{t('deviceScripts.noLocal')}</p>}
        {state.loaded && !state.catalog?.scripts.some(item => item.effect === 'read_only') && <p className="text-sm leading-6 text-gray-500">{t('deviceScripts.noScripts')}</p>}
        <label className="block text-sm text-gray-700 dark:text-gray-200">{t('deviceScripts.choose')}
          <select value={form.scriptId} disabled={waiting || device.status !== 'online'} onChange={event => choose(event.target.value)} className={inputClass}>
            <option value="">{t('deviceScripts.select')}</option>
            {state.catalog?.scripts.map(item => <option key={item.script_id} value={item.script_id} disabled={item.effect !== 'read_only'}>{item.title} · {item.version}{item.effect !== 'read_only' ? ` · ${t('deviceScripts.writeUnavailable')}` : ''}</option>)}
          </select>
        </label>
        {selected && <>
          <dl className="grid gap-3 text-xs text-gray-500 sm:grid-cols-2">
            <div><dt>{t('deviceScripts.inputs')}</dt><dd className="mt-1 break-words text-gray-700 dark:text-gray-200">{selected.parameters.map(field => `${field.name} (${t(`deviceScripts.kind.${field.kind}`)}${field.required ? `, ${t('deviceScripts.required')}` : ''})`).join(' · ') || t('deviceScripts.noInputs')}</dd></div>
            <div><dt>{t('deviceScripts.outputs')}</dt><dd className="mt-1 break-words text-gray-700 dark:text-gray-200">{selected.result_fields.map(field => field.name).join(' · ')}</dd></div>
          </dl>
          <details className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
          <summary className="cursor-pointer text-sm text-gray-700 dark:text-gray-200">{t('deviceScripts.advanced')}</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <label className="text-sm text-gray-700 dark:text-gray-200">{t('deviceScripts.timeout')}
              <input type="number" min={1} max={selected.timeout_seconds} disabled={waiting} value={form.timeoutSeconds} onChange={event => setForm({ ...form, timeoutSeconds: Number(event.target.value) })} className={inputClass} />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-200">{t('deviceScripts.outputLimit')}
              <input type="number" min={256} max={selected.max_output_bytes} disabled={waiting} value={form.maxOutputBytes} onChange={event => setForm({ ...form, maxOutputBytes: Number(event.target.value) })} className={inputClass} />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-200">{t('deviceScripts.runLimit')}
              <input type="number" min={1} max={selected.max_runs} disabled={waiting} value={form.maxRuns} onChange={event => setForm({ ...form, maxRuns: Number(event.target.value) })} className={inputClass} />
            </label>
          </div>
          <p className="text-xs leading-6 text-gray-500">{t('deviceScripts.limitsHint', { seconds: selected.timeout_seconds, bytes: selected.max_output_bytes, runs: selected.max_runs })}</p>
          </details>
        </>}
        {!draftSaved && <p className="text-xs text-amber-600">{t('deviceScripts.savePurpose')}</p>}
        <div className="flex flex-wrap gap-3">
          <button type="button" disabled={waiting || !payload || !draftSaved || device.status !== 'online'} onClick={() => void state.publish(form)} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{t(`deviceScripts.${state.busy ? 'saving' : 'publish'}`)}</button>
          {active && <button type="button" disabled={waiting} aria-expanded={Boolean(confirmation)} aria-controls="device-script-revoke" onClick={() => { if (state.policy) setConfirmation({ revision: state.policy.policy.revision, name: currentName }); }} className="rounded-xl px-3 py-2 text-sm text-red-600 disabled:opacity-50">{t('deviceScripts.revoke')}</button>}
        </div>
        {confirmation && <div id="device-script-revoke" className="space-y-3 rounded-xl border border-red-200 p-3 dark:border-red-900">
          <p role="alert" className="text-sm leading-6 text-gray-600 dark:text-gray-300">{t('deviceScripts.currentPolicy', confirmation)} · {t('deviceScripts.revokeHint')}</p>
          <div className="flex flex-wrap gap-3"><button type="button" disabled={waiting} onClick={async () => { await state.revoke(confirmation.revision); setConfirmation(null); }} className="rounded-xl bg-red-600 px-3 py-2 text-sm text-white disabled:opacity-50">{t('deviceScripts.confirmRevoke')}</button>
            <button type="button" disabled={state.busy} onClick={() => setConfirmation(null)} className="px-3 py-2 text-sm text-gray-500">{t('deviceScripts.cancel')}</button></div>
        </div>}
        <p className="rounded-xl bg-blue-50 p-3 text-xs leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{t('deviceScripts.notTested')}</p>
      </>}
    </>}
  </section>;
}
