import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useAgentDeviceScriptStore } from '@/stores/agentDeviceScriptStore';
import { useAgentDeviceScriptCasesStore } from '@/stores/agentDeviceScriptCasesStore';
import AgentDeviceScriptCases from './AgentDeviceScriptCases';
import AgentDeviceScriptEvidence from './AgentDeviceScriptEvidence';
import type { AgentScriptCaseRecord } from '@/types/agentDeviceScriptCases';
import { useAgentBusinessCurrentStateStore } from '@/stores/agentBusinessCurrentStateStore';
import { parseScriptFields } from '@/utils/agentDeviceScriptValidation';
import type { ScriptParameter } from '@/types/deviceScriptPolicy';

const inputClass = 'mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
function BusinessFields({ fields, values, onChange, prefix, resultText = false }: {
  fields: ScriptParameter[]; values: Record<string, string>; onChange: (values: Record<string, string>) => void; prefix: string; resultText?: boolean;
}) {
  const { t } = useTranslation();
  return <div className="grid gap-3 sm:grid-cols-2">{fields.map(field => <label key={field.name} htmlFor={`${prefix}-${field.name}`} className="block min-w-0 text-sm text-gray-700 dark:text-gray-200">
    <span className="break-words">{field.name}</span> <span className="text-xs text-gray-500">{t(`agentDeviceScripts.${field.required ? 'required' : 'optional'}`)}</span>
    {field.kind === 'boolean' || field.choices.length > 0 ? <select id={`${prefix}-${field.name}`} value={values[field.name] ?? ''}
      onChange={event => onChange({ ...values, [field.name]: event.target.value })} className={inputClass}>
      <option value="">{t('agentDeviceScripts.boolean.choose')}</option>
      {field.kind === 'boolean' ? <><option value="true">{t('agentDeviceScripts.boolean.true')}</option><option value="false">{t('agentDeviceScripts.boolean.false')}</option></>
        : field.choices.map(value => <option key={value} value={value}>{value}</option>)}
    </select> : resultText && field.kind === 'string' ? <textarea id={`${prefix}-${field.name}`} value={values[field.name] ?? ''} rows={3} autoComplete="off"
      maxLength={field.max_length} onChange={event => onChange({ ...values, [field.name]: event.target.value })} className={inputClass} /> : <input id={`${prefix}-${field.name}`} value={values[field.name] ?? ''} autoComplete="off"
      inputMode={field.kind === 'integer' ? 'numeric' : 'text'} maxLength={field.kind === 'string' ? field.max_length : 17}
      onKeyDown={event => { if (event.key === 'Enter') event.preventDefault(); }}
      onChange={event => onChange({ ...values, [field.name]: event.target.value })} className={inputClass} />}
  </label>)}</div>;
}

function ScriptConfiguration({ agentId, active, disabled }: { agentId: string; active: boolean; disabled: boolean }) {
  const { t } = useTranslation(), state = useAgentDeviceScriptStore(), label = useId();
  const cases = useAgentDeviceScriptCasesStore();
  const snapshot = useAgentBusinessCurrentStateStore(value => value.agentId === agentId ? value.snapshot : null);
  const paused = snapshot ? !snapshot.active : !active;
  const [inputs, setInputs] = useState<Record<string, string>>({}), [expected, setExpected] = useState<Record<string, string>>({});
  const [invalid, setInvalid] = useState(false);
  const [caseTitle, setCaseTitle] = useState(''), [editingCase, setEditingCase] = useState<AgentScriptCaseRecord | null>(null);
  const { select, reset } = state;
  const { select: selectCases, reset: resetCases } = cases;
  useEffect(() => { void select(agentId); return () => reset(); }, [agentId, select, reset]);
  useEffect(() => { void selectCases(agentId); return () => resetCases(); }, [agentId, selectCases, resetCases]);
  useEffect(() => { setInputs({}); setExpected({}); setInvalid(false); setCaseTitle(''); setEditingCase(null); }, [state.identity, state.deviceId, state.descriptor?.grant_digest, state.binding?.revision]);
  const text = (name: string) => t(`agentDeviceScripts.${name}`);
  const waiting = disabled || state.busy || state.loading || state.loadingDevice || state.checking || state.cancelling || cases.busy;
  const blocked = waiting || !paused || state.unknown || Boolean(state.bindingPending) || Boolean(cases.pending) || !cases.storageReady;
  const bindingMatches = Boolean(state.binding?.enabled && state.binding.device_id === state.deviceId
    && state.binding.policy_digest === state.policy?.policy.digest && state.binding.grant_digest === state.descriptor?.grant_digest);
  const start = () => {
    if (!state.descriptor || blocked || !bindingMatches) return;
    const parameters = parseScriptFields(state.descriptor.parameters, inputs), results = parseScriptFields(state.descriptor.result_fields, expected, true);
    if (!parameters || !results || !Object.keys(results).length) { setInvalid(true); return; }
    setInputs({}); setExpected({}); setInvalid(false); void state.start(parameters, results);
  };
  const saveCase = () => {
    if (!state.descriptor || blocked || !bindingMatches) return;
    const parameters = parseScriptFields(state.descriptor.parameters, inputs), results = parseScriptFields(state.descriptor.result_fields, expected, true);
    if (!parameters || !results || !Object.keys(results).length || !caseTitle.trim()) { setInvalid(true); return; }
    const title = caseTitle, editing = editingCase;
    setInputs({}); setExpected({}); setInvalid(false); setCaseTitle(''); setEditingCase(null);
    void cases.save(title, parameters, results, editing);
  };
  return <div className="mt-4 space-y-4">
    <p className="text-sm leading-6 text-gray-500 dark:text-gray-400">{text('hint')}</p>
    {!paused && <p className="text-sm text-amber-700 dark:text-amber-300">{text('pause')}</p>}
    {(state.loading || state.busy) && <p role="status" className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" />{text('loading')}</p>}
    {state.error && <p role="alert" className="text-sm text-red-600">{text(`errors.${state.error}`)}</p>}
    {(state.unknown || state.bindingPending) && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm leading-6 text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">{text('unknown')}</p>}
    {state.bindingPending && <p className="break-all text-xs text-gray-500">{t('agentDeviceScripts.request', { id: state.bindingPending.requestId })}</p>}
    {state.requestState && <div role="status" className="space-y-1 text-sm text-gray-600 dark:text-gray-300">
      <p>{text(`requestStatus.${state.requestState.status}`)}</p>
      {state.requestState.status === 'ready' && <p>{text(`stopStatus.${state.requestState.stop_status}`)}</p>}
      {state.requestState.error_code && <p>{text(`preparationError.${state.requestState.error_code}`)}</p>}
    </div>}
    <label htmlFor={`${label}-device`} className="block text-sm font-medium text-gray-700 dark:text-gray-200">{text('device')}
      <select id={`${label}-device`} className={inputClass} disabled={blocked} value={state.deviceId} onChange={event => void state.chooseDevice(event.target.value)}>
        <option value="">{text('choose')}</option>{state.devices.map(device => <option key={device.id} value={device.id} disabled={device.status !== 'online'}>{device.device_name}</option>)}
      </select>
    </label>
    {state.hasMore && <button type="button" disabled={waiting || state.loadingMore} onClick={() => void state.loadMoreDevices()} className="text-sm text-blue-600 disabled:opacity-40">{text('more')}</button>}
    {state.deviceId && !state.descriptor && !state.loadingDevice && <p className="text-xs leading-6 text-gray-500">{text('unavailable')}</p>}
    {state.binding && <p className="text-sm text-gray-700 dark:text-gray-200">{state.binding.enabled ? t('agentDeviceScripts.binding', { script: state.binding.script_id, revision: state.binding.revision }) : text('retired')}</p>}
    <div className="flex flex-wrap items-center gap-3">
      <button type="button" disabled={blocked || !state.descriptor || bindingMatches} onClick={() => void state.saveBinding(true)} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{text('save')}</button>
      {state.binding?.enabled && <button type="button" disabled={blocked} onClick={() => void state.saveBinding(false)} className="text-sm text-red-600 disabled:opacity-40">{text('remove')}</button>}
      <button type="button" disabled={waiting} onClick={() => void state.refresh()} className="text-sm text-blue-600 disabled:opacity-40">{text('refresh')}</button>
      <Link to="/ai/device-control" className="text-sm text-blue-600">{text('manage')}</Link>
    </div>
    {state.descriptor && <fieldset disabled={blocked || !bindingMatches} className="space-y-4 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
      <legend className="px-2 text-sm font-medium text-gray-800 dark:text-white">{state.descriptor.title}</legend>
      <h4 className="text-sm font-medium text-gray-700 dark:text-gray-200">{text('inputs')}</h4>
      {state.descriptor.parameters.length ? <BusinessFields fields={state.descriptor.parameters} values={inputs} onChange={setInputs} prefix={`${label}-input`} /> : <p className="text-xs text-gray-500">{text('noInputs')}</p>}
      <h4 className="text-sm font-medium text-gray-700 dark:text-gray-200">{text('expected')}</h4>
      <BusinessFields fields={state.descriptor.result_fields} values={expected} onChange={setExpected} prefix={`${label}-expected`} resultText />
      {invalid && <p role="alert" className="text-sm text-red-600">{text('invalid')}</p>}
      <p className="text-xs leading-6 text-gray-500">{text('private')}</p>
      <button type="button" onClick={start} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{text('run')}</button>
      <div className="space-y-3 border-t border-gray-200 pt-3 dark:border-gray-700">
        <label htmlFor={`${label}-case-title`} className="block text-sm text-gray-700 dark:text-gray-200">{text('cases.name')}
          <input id={`${label}-case-title`} value={caseTitle} maxLength={80} autoComplete="off" className={inputClass}
            onKeyDown={event => { if (event.key === 'Enter') event.preventDefault(); }} onChange={event => setCaseTitle(event.target.value)} /></label>
        {editingCase && <p className="break-words text-xs text-gray-500">{t('agentDeviceScripts.cases.editing', { title: editingCase.title, revision: editingCase.revision })}</p>}
        <p className="text-xs leading-6 text-gray-500">{text('cases.saveHint')}</p>
        <div className="flex flex-wrap gap-4"><button type="button" disabled={!caseTitle.trim()} onClick={saveCase} className="text-sm font-medium text-blue-600 disabled:opacity-40">{text(editingCase ? 'cases.saveVersion' : 'cases.save')}</button>
          {editingCase && <button type="button" onClick={() => { setEditingCase(null); setCaseTitle(''); setInputs({}); setExpected({}); }} className="text-sm text-gray-500">{text('cases.new')}</button>}</div>
      </div>
    </fieldset>}
    <AgentDeviceScriptCases agentId={agentId} disabled={blocked || !bindingMatches} onEdit={body => {
      setEditingCase(body.case); setCaseTitle(body.case.title); setInvalid(false);
      setInputs(Object.fromEntries(Object.entries(body.values.parameters).map(([name, value]) => [name, String(value)])));
      setExpected(Object.fromEntries(Object.entries(body.values.expected_fields).map(([name, value]) => [name, String(value)])));
    }} />
    {(state.bindingPending || state.trialPointer) && <div className="flex flex-wrap gap-4">
      <button type="button" disabled={disabled || state.checking || state.cancelling || state.loading || Boolean(state.bindingPending && state.busy)} onClick={() => void state.recover()} className="text-sm font-medium text-blue-600 disabled:opacity-40">{text('recover')}</button>
      {state.trialPointer && state.unknown && !state.bindingPending && <button type="button" disabled={disabled || state.cancelling} onClick={() => void state.cancel()} className="text-sm font-medium text-red-600 disabled:opacity-40">{text(state.cancelling ? 'stopping' : 'cancel')}</button>}
    </div>}
    {state.trialPointer && state.unknown && <p className="text-xs leading-6 text-gray-500">{text('cancelHint')}</p>}
    {state.trialPointer && <p className="break-all text-xs text-gray-500">{t('agentDeviceScripts.request', { id: state.trialPointer.requestId })}</p>}
    {state.report && <section aria-label={text('comparison')} className="space-y-2 rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/40">
      <h4 className="font-medium text-gray-900 dark:text-white">{text(`status.${state.report.status}`)}</h4>
      {state.report.error_code && <p className="text-sm text-amber-700 dark:text-amber-300">{text(`errorCode.${state.report.error_code}`)}</p>}
      <p className="text-sm text-gray-600 dark:text-gray-300">{t('agentDeviceScripts.applicability', { state: text(`current.${state.report.applicability}`) })}</p>
      {state.report.remaining_runs !== null && <p className="text-sm text-gray-600 dark:text-gray-300">{t('agentDeviceScripts.remaining', { count: state.report.remaining_runs })}</p>}
      <p className="break-all text-xs text-gray-500">{t('agentDeviceScripts.execution', { id: state.report.execution_id })}</p>
      <AgentDeviceScriptEvidence key={`${state.identity}:${state.report.id}`} agentId={agentId} report={state.report} />
    </section>}
    <p className="text-xs leading-6 text-gray-500">{text('reportHint')}</p>
  </div>;
}

export default function AgentDeviceScriptPanel({ agentId, active, disabled = false }: { agentId: string; active: boolean; disabled?: boolean }) {
  const { t } = useTranslation(), role = useAuthStore(state => state.user?.role), [expanded, setExpanded] = useState(false);
  if (role !== 'admin') return null;
  return <details onToggle={event => setExpanded(event.currentTarget.open)} className="rounded-3xl border border-gray-100 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
    <summary className="cursor-pointer text-sm font-semibold text-gray-900 dark:text-white">{t('agentDeviceScripts.title')}</summary>
    {expanded && <ScriptConfiguration agentId={agentId} active={active} disabled={disabled} />}
  </details>;
}
