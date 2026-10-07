import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AudioLines, Brain, FileSearch, ScanText, Sparkles } from 'lucide-react';
import { operationsApi } from '../../services/operationsApi';
import type { SharedConnectionResult } from '../../types/sharedModels';
import OperationsSharedModels from '../settings/OperationsSharedModels';
import OperationsModelUsage from '../settings/OperationsModelUsage';
import { OpsFeedback, OpsPanel, opsButton, useOpsResource } from './ui';

const purposes = [['chat', Brain], ['embedding', FileSearch], ['asr', AudioLines], ['ocr', ScanText], ['vlm', Sparkles]] as const;

function CurrentModels() {
  const { t } = useTranslation();
  const resource = useOpsResource(operationsApi.sharedModels);
  const [testing, setTesting] = useState('');
  const [results, setResults] = useState<Record<string, SharedConnectionResult>>({});
  const [error, setError] = useState('');
  const test = async (id: string) => {
    if (!resource.data || testing) return; setTesting(id); setError('');
    try { const result = await operationsApi.testSharedProvider(id, resource.data.version); setResults(previous => ({ ...previous, [id]: result })); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t('opsWorkspace.error')); }
    finally { setTesting(''); }
  };
  return <div className="space-y-5"><div className="flex justify-end"><button className={opsButton} disabled={resource.busy || !!testing} onClick={() => { setResults({}); resource.reload(); }}>{t('opsWorkspace.refresh')}</button></div><OpsFeedback {...resource} />
    {resource.data && <>
      <OpsPanel><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">{t('opsWorkspace.version', { version: resource.data.version })}</h2><p role="status" className={`text-sm ${resource.data.synchronization === 'failed' ? 'text-amber-800' : 'text-emerald-800'}`}>{t(!resource.data.enabled ? 'opsWorkspace.missing' : resource.data.synchronization === 'synced' ? 'opsWorkspace.syncComplete' : resource.data.synchronization === 'failed' ? 'opsWorkspace.syncFailed' : 'opsWorkspace.syncPending')}</p></div></OpsPanel>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{purposes.map(([purpose, Icon]) => {
        const selection = resource.data?.defaults[purpose];
        const provider = resource.data?.providers.find(item => item.id === selection?.provider_id);
        return <OpsPanel key={purpose}><div className="flex items-center gap-3"><Icon className="h-5 w-5 text-indigo-500" aria-hidden="true" /><h3 className="font-semibold">{t(`sharedModels.purposes.${purpose}`)}</h3></div><p className="mt-5 break-all text-sm font-medium">{selection?.model_id || t('opsWorkspace.missing')}</p><p className="mt-2 text-xs text-slate-500">{provider?.name || '—'}</p><p className="mt-4 text-xs text-slate-500">{t('opsWorkspace.credential')}: {t(provider?.has_api_key ? 'opsWorkspace.configured' : 'opsWorkspace.missing')}</p></OpsPanel>;
      })}</div>
      <OpsPanel><h2 className="font-semibold">{t('opsWorkspace.connectionTest')}</h2><p className="mt-2 text-sm leading-6 text-slate-500">{t('opsWorkspace.testHint')}</p>{error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}<div className="mt-5 divide-y divide-slate-100">{resource.data.providers.map(provider => <div key={provider.id} className="flex flex-wrap items-center justify-between gap-4 py-4"><div><h3 className="text-sm font-medium">{provider.name}</h3><p className="mt-1 break-all text-xs text-slate-500">{provider.api_base_url}</p>{results[provider.id] && <p role="status" className={`mt-2 text-xs ${results[provider.id].success ? 'text-emerald-800' : 'text-amber-800'}`}>{results[provider.id].message}</p>}</div><button className={opsButton} disabled={!!testing || !provider.is_active || !provider.has_api_key} onClick={() => void test(provider.id)}>{t(testing === provider.id ? 'opsWorkspace.loading' : 'opsWorkspace.connectionTest')}</button></div>)}</div></OpsPanel>
    </>}
  </div>;
}

export default function OperationsModelCenter() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab = ['summary', 'services', 'usage'].find(value => value === params.get('tab')) || 'summary';
  return <div className="space-y-5"><div role="group" className="flex flex-wrap gap-2">{['summary', 'services', 'usage'].map(value => <button key={value} aria-pressed={tab === value} className={`${opsButton} ${tab === value ? '!border-indigo-200 !bg-indigo-50 !text-indigo-700' : ''}`} onClick={() => setParams({ tab: value })}>{t(`opsWorkspace.${value}`)}</button>)}</div>{tab === 'summary' ? <CurrentModels /> : tab === 'services' ? <OperationsSharedModels /> : <OperationsModelUsage />}</div>;
}
