import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { FiLoader, FiPlus } from 'react-icons/fi';
import type { AIModelConfig } from '@/stores/providersStore';
import AIProvidersApiService, { type ModelType, type ProviderConnectionDraft } from '@/services/aiProvidersApi';
interface Props { connection: ProviderConnectionDraft; models: AIModelConfig[]; onChange: (models: AIModelConfig[]) => void; disabled?: boolean; suggestions?: AIModelConfig[]; lockedModelIds?: string[]; focusModelId?: string; beforeFetch?: () => boolean; onBusyChange?: (busy: boolean) => void; }
const types: ModelType[] = ['chat', 'embedding', 'vlm', 'asr', 'ocr'];
export default function ProviderModelPicker({ connection, models, onChange, disabled, suggestions = [], lockedModelIds = [], focusModelId, beforeFetch, onBusyChange }: Props) {
  const { t } = useTranslation();
  const [remote, setRemote] = useState<AIModelConfig[]>([]);
  const [initialModels] = useState(models);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { success: boolean; message: string }>>({});
  const [error, setError] = useState('');
  const [manual, setManual] = useState(false);
  const [modelId, setModelId] = useState('');
  const [modelType, setModelType] = useState<ModelType>('chat');
  const version = useRef(0);
  const fingerprint = JSON.stringify(connection);
  useEffect(() => { onBusyChange?.(loading || !!testing); return () => onBusyChange?.(false); }, [loading, testing, onBusyChange]);
  useEffect(() => {
    version.current += 1; setRemote([]); setResults({}); setError(''); setLoading(false); setTesting(null);
    return () => { version.current += 1; };
  }, [fingerprint]);
  const fetchModels = async () => {
    if (beforeFetch && !beforeFetch()) return;
    const current = version.current; setLoading(true); setError('');
    try {
      const result = await new AIProvidersApiService().previewModels(connection);
      if (current !== version.current) return;
      setRemote((result.models || []).map(model => ({ id: model.id, name: model.name || model.id,
        type: types.includes(model.model_type as ModelType) ? model.model_type as ModelType : 'chat', capabilities: model.capabilities || undefined })));
      if (!result.models?.length) setError(t('modelSetup.noModels'));
    } catch (err) { if (current === version.current) setError(err instanceof Error ? err.message : t('common.loadFailed')); }
    finally { if (current === version.current) setLoading(false); }
  };
  const probe = async (model: AIModelConfig) => {
    if (beforeFetch && !beforeFetch()) return;
    const current = version.current; setTesting(model.id);
    try {
      const result = await new AIProvidersApiService().probeModel({ ...connection, model_id: model.id, model_type: model.type });
      if (current === version.current) setResults(previous => ({ ...previous, [model.id]: { success: result.success === true, message: result.message || t('modelSetup.testFailed') } }));
    } catch (err) { if (current === version.current) setResults(previous => ({ ...previous, [model.id]: { success: false, message: err instanceof Error ? err.message : t('modelSetup.testFailed') } })); }
    finally { if (current === version.current) setTesting(null); }
  };
  const catalog = remote.length ? remote : suggestions;
  const available = [...catalog, ...initialModels.filter(model => !catalog.some(item => item.id === model.id))];
  const options = [...models, ...available.filter(model => !models.some(selected => selected.id === model.id))].sort((a, b) => Number(b.id === focusModelId) - Number(a.id === focusModelId));
  return <section className="space-y-3">
    <div className="flex items-center justify-between gap-3 flex-wrap"><h3 className="font-semibold dark:text-gray-100">{t('modelSetup.selectModels')}</h3>
      <button type="button" disabled={disabled || loading || !!testing} onClick={fetchModels} className="px-3 py-2 rounded-lg bg-blue-600 text-white text-sm disabled:opacity-50 inline-flex items-center gap-2 whitespace-nowrap">{loading && <FiLoader className="animate-spin" />}{t('modelSetup.fetchModels')}</button></div>
    <p className="text-xs text-gray-500 dark:text-gray-400">{t('modelSetup.testHint')}</p>
    <p className="text-xs text-gray-500 dark:text-gray-400">{t('modelSetup.modelTypeHint')}</p>
    {error && <p role="alert" className="text-sm text-amber-600 dark:text-amber-400">{error}</p>}
    <div className="max-h-56 overflow-y-auto divide-y divide-gray-200 dark:divide-gray-700 border border-gray-200 dark:border-gray-700 rounded-lg">
      {!options.length && <p className="p-4 text-sm text-gray-500">{t('modelSetup.modelEmpty')}</p>}
      {options.map(model => <div key={model.id} className={`p-3 space-y-2 ${model.id === focusModelId ? 'bg-blue-50 dark:bg-blue-900/20' : ''}`}><div className="flex items-center gap-3">
          <input type="checkbox" aria-label={model.id} checked={models.some(selected => selected.id === model.id)} disabled={disabled || !!testing} onChange={event => onChange(event.target.checked ? [...models, model] : models.filter(selected => selected.id !== model.id))} />
          <span className="text-sm break-all flex-1 min-w-0 dark:text-gray-100">{model.id}</span>
          {models.some(selected => selected.id === model.id) && !lockedModelIds.includes(model.id) ? <select
            aria-label={t('modelSetup.modelTypeFor', { model: model.id })} value={model.type} disabled={disabled || loading || !!testing}
            onChange={event => { onChange(models.map(selected => selected.id === model.id ? { ...selected, type: event.target.value as ModelType } : selected)); setResults(previous => { const next = { ...previous }; delete next[model.id]; return next; }); }}
            className="max-w-36 rounded border dark:border-gray-600 dark:bg-gray-800 text-xs text-blue-600 dark:text-blue-300 p-1">
            {types.map(type => <option key={type} value={type}>{t(`modelSetup.types.${type}`)}</option>)}
          </select> : <span className="text-xs text-blue-600 dark:text-blue-300">{t(`modelSetup.types.${model.type}`)}</span>}
          <button type="button" disabled={disabled || !!testing || loading || !['chat', 'embedding'].includes(model.type)} onClick={() => probe(model)} className="text-blue-600 dark:text-blue-400 text-xs whitespace-nowrap disabled:opacity-40">{testing === model.id ? t('modelSetup.testing') : t('modelSetup.test')}</button></div>
        {results[model.id] && <p role="status" className={`text-xs ${results[model.id].success ? 'text-green-600' : 'text-red-500'}`}>{results[model.id].message}</p>}</div>)}
    </div>
    <button type="button" onClick={() => setManual(!manual)} className="inline-flex items-center gap-1 text-sm text-blue-600 dark:text-blue-400"><FiPlus />{t('modelSetup.manual')}</button>
    {manual && <div className="flex flex-wrap gap-2"><input aria-label={t('modelSetup.modelId')} placeholder={t('modelSetup.modelId')} value={modelId} onChange={event => setModelId(event.target.value)} className="flex-1 min-w-32 rounded-lg border dark:border-gray-600 dark:bg-gray-800 dark:text-white px-3 py-2 text-sm" />
      <select aria-label={t('modelSetup.modelType')} value={modelType} onChange={event => setModelType(event.target.value as ModelType)} className="rounded-lg border dark:border-gray-600 dark:bg-gray-800 dark:text-white p-2 text-sm">{types.map(type => <option key={type} value={type}>{t(`modelSetup.types.${type}`)}</option>)}</select>
      <button type="button" disabled={disabled || !modelId.trim() || models.some(model => model.id === modelId.trim())} onClick={() => { onChange([...models, { id: modelId.trim(), name: modelId.trim(), type: modelType }]); setModelId(''); }} className="px-3 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-40 text-sm">{t('common.add')}</button></div>}
  </section>;
}
