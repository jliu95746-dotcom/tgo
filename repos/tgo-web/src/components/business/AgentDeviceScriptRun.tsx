import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAgentDeviceScriptRunStore } from '@/stores/agentDeviceScriptRunStore';
import type { ScriptBusinessValues } from '@/types/agentDeviceScripts';
import type { ScriptRunView } from '@/types/agentDeviceScriptRuns';

interface RunProps {
  agentId: string;
  reportId: string;
  reviewId: string | null;
  inputs: ScriptBusinessValues | null;
  ready: boolean;
  onStart: () => void;
}

export default function AgentDeviceScriptRun({ agentId, reportId, reviewId, inputs, ready, onStart }: RunProps) {
  const { t } = useTranslation();
  const state = useAgentDeviceScriptRunStore();
  const { select } = state;
  const [fields, setFields] = useState<ScriptBusinessValues | null>(null);
  const [resultRead, setResultRead] = useState(false);
  useEffect(() => { void select(agentId); }, [agentId, select]);
  const selected = state.agentId === agentId;
  const text = (name: string) => t(`agentDeviceScripts.businessRun.${name}`);
  const terminal = state.receipt && ['succeeded', 'failed', 'cancelled'].includes(state.receipt.status);
  const busy = state.starting || state.querying || state.cancelling;
  const run = () => {
    if (!reviewId || inputs === null || !ready) return;
    setFields(null);
    setResultRead(false);
    onStart();
    void state.start(reportId, reviewId, inputs).then(accept);
  };
  const accept = (value: ScriptRunView | null) => {
    setFields(value?.result_fields ?? null);
    setResultRead(value !== null);
  };
  const recover = () => { setFields(null); setResultRead(false); void state.recover().then(accept); };
  const cancel = () => { setFields(null); setResultRead(false); void state.cancel().then(accept); };
  return <section aria-label={text('title')} className="space-y-2 border-t border-gray-200 pt-3 dark:border-gray-700">
    <h5 className="font-medium text-gray-900 dark:text-white">{text('title')}</h5>
    <p className="text-xs leading-5 text-gray-500">{text('hint')}</p>
    <button type="button" onClick={run}
      disabled={!selected || !ready || !reviewId || inputs === null || !state.storageReady || busy || Boolean(state.pointer && !terminal)}
      className="text-blue-600 disabled:opacity-40">{text(state.starting ? 'starting' : 'start')}</button>
    {selected && state.pointer && <div className="space-y-2">
      <p className="break-all text-xs text-gray-500">{text('request')} {state.pointer.request_id}</p>
      <div className="flex flex-wrap gap-4">
        <button type="button" onClick={recover} disabled={state.querying || state.cancelling}
          className="text-blue-600 disabled:opacity-40">{text(state.querying ? 'reading' : 'read')}</button>
        {!terminal && <button type="button" onClick={cancel} disabled={state.cancelling}
          className="text-red-600 disabled:opacity-40">{text(state.cancelling ? 'cancelling' : 'cancel')}</button>}
      </div>
    </div>}
    {selected && state.error && <p role="alert" className="text-red-600">{text(`error.${state.error}`)}</p>}
    {selected && state.receipt && <p role="status">{text(`status.${state.receipt.status}`)}</p>}
    {selected && state.receipt?.status === 'unknown' && state.receipt.stop_requested_at && <p className="text-xs text-gray-500">{text('stopPending')}</p>}
    {selected && state.receipt?.error_code && <p className="text-xs text-gray-500">{text(`reason.${state.receipt.error_code}`)}</p>}
    {fields && <dl className="space-y-2 text-sm">{Object.entries(fields).map(([name, value]) =>
      <div key={name}><dt className="break-all font-medium text-gray-600 dark:text-gray-300">{name}</dt>
        <dd className="whitespace-pre-wrap break-words text-gray-900 dark:text-white">{String(value)}</dd></div>)}</dl>}
    {selected && state.receipt?.status === 'succeeded' && !fields && <p className="text-xs text-gray-500">{text(resultRead ? 'bodyUnavailable' : 'bodyHint')}</p>}
  </section>;
}
