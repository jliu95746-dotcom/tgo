import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { X, Loader2, CheckCircle2, Circle } from 'lucide-react';
import { getDeviceTools } from '@/services/deviceControlApi';
import { summarizeDeviceTools } from '@/utils/businessScenarios';
import type { Device } from '@/types/deviceControl';
import type { DeviceToolDescription } from '@/types/businessScenarios';
import type { DeviceScenarioId } from '@/types/deviceSetup';
import { useDialogFocus } from '@/hooks/useDialogFocus';

export default function DeviceCapabilitiesDialog({ device, onClose, onConfigure }: {
  device: Device; onClose: () => void; onConfigure?: (scenario: DeviceScenarioId) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [tools, setTools] = useState<DeviceToolDescription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const dialogRef = useDialogFocus<HTMLElement>(onClose);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true); setTools([]); setError('');
    getDeviceTools(device.id, { signal: abort.signal }).then(result => { if (!abort.signal.aborted) setTools(result.tools); })
      .catch(() => { if (!abort.signal.aborted) setError(t('businessScenarios.device.unavailable')); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [device.id, refresh, t]);
  const summary = summarizeDeviceTools(tools);
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="device-capability-title" className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-3xl bg-white p-6 shadow-2xl dark:bg-gray-900">
      <div className="flex items-center justify-between"><h3 id="device-capability-title" className="text-lg font-bold text-gray-900 dark:text-white">{t('businessScenarios.device.titleInspect')} · {device.device_name}</h3><button onClick={onClose} aria-label={t('businessScenarios.close')} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100"><X size={20} /></button></div>
      <p className="mt-4 rounded-xl bg-blue-50 p-3 text-xs leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{t('businessScenarios.device.caution')}</p>
      {loading ? <p className="mt-6 flex items-center gap-2 text-sm text-gray-500"><Loader2 className="animate-spin" size={18} />{t('businessScenarios.device.loading')}</p> : error ? <p role="alert" className="mt-5 text-sm text-red-600">{error}</p> : <>
        <div className="my-5 grid grid-cols-3 gap-3">{(['files', 'commands', 'screen'] as const).map(key => <div key={key} className="rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-800"><p className="text-gray-700 dark:text-gray-200">{t(`businessScenarios.device.${key}`)}</p><p className="mt-2 flex items-center gap-1 text-gray-500">{summary[key] ? <CheckCircle2 size={14} className="text-green-500" /> : <Circle size={14} />}{t(`businessScenarios.device.${summary[key] ? 'detected' : 'missing'}`)}</p></div>)}</div>
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-200">{t('businessScenarios.device.reported')}</h4>
        {!tools.length && <p className="mt-3 text-sm text-gray-500">{t('businessScenarios.device.empty')}</p>}
        <ul className="mt-3 space-y-2">{tools.map(tool => <li key={tool.name} className="rounded-xl border border-gray-200 p-3 dark:border-gray-700"><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{tool.name}</p><p className="mt-1 break-words text-xs text-gray-500">{tool.description}</p></li>)}</ul>
      </>}
      <div className="mt-6 flex flex-wrap justify-end gap-2">
        <button disabled={loading} onClick={() => setRefresh(value => value + 1)} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300">{t('businessScenarios.device.retry')}</button>
        {onConfigure ? (['files', 'report'] as const).map(scenario => <button key={scenario}
          disabled={scenario === 'files' && (loading || Boolean(error) || !summary.files)}
          onClick={() => onConfigure(scenario)} className="rounded-xl bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t(`businessScenarios.deviceGuide.configureActions.${scenario}`)}</button>)
          : <button disabled={loading || Boolean(error) || !tools.length} onClick={() => { onClose(); navigate('/ai/agents'); }} className="rounded-xl bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t('businessScenarios.device.bind')}</button>}
      </div>
    </section>
  </div>;
}
