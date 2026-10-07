import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { CompanyMigrationPreview } from '../../types/operations';
import type {
  ModelPurpose, SharedModels, SharedProviderChange, SharedSelection,
} from '../../types/sharedModels';

const purposes: ModelPurpose[] = ['chat', 'embedding', 'asr', 'ocr', 'vlm'];
const field = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm';
const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-50';
const blankDefaults = (): Record<ModelPurpose, SharedSelection> => ({
  chat: { provider_id: '', model_id: '' },
  embedding: { provider_id: '', model_id: '' },
  asr: { provider_id: '', model_id: '' },
  ocr: { provider_id: '', model_id: '' },
  vlm: { provider_id: '', model_id: '' },
});

export default function OperationsSharedModels() {
  const { t } = useTranslation();
  const [current, setCurrent] = useState<SharedModels | null>(null);
  const [providers, setProviders] = useState<SharedProviderChange[]>([]);
  const [defaults, setDefaults] = useState<Record<ModelPurpose, SharedSelection>>(blankDefaults);
  const [projects, setProjects] = useState<CompanyMigrationPreview[]>([]);
  const [source, setSource] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const apply = useCallback((value: SharedModels) => {
    setCurrent(value);
    setProviders(value.providers.map(({ has_api_key: _hasKey, ...provider }) => provider));
    setDefaults({ ...blankDefaults(), ...value.defaults });
    setReason('');
  }, []);

  const load = useCallback(async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const models = await operationsApi.sharedModels();
      apply(models);
      if (!models.enabled) {
        const preview = await operationsApi.preview();
        setProjects(preview.data);
        setSource(value => value || preview.data[0]?.project_id || '');
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('sharedModels.error'));
    } finally { setBusy(false); }
  }, [apply, t]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (current && window.location.hash === '#shared-models') {
      document.getElementById('shared-models')?.scrollIntoView();
    }
  }, [current]);

  const updateProvider = (index: number, next: SharedProviderChange) => {
    setProviders(previous => previous.map((item, position) => position === index ? next : item));
  };
  const addProvider = () => setProviders(previous => [...previous, {
    id: crypto.randomUUID(), provider: 'custom', name: '', api_base_url: '',
    config: null, default_model: null, is_active: true, models: [],
  }]);
  const run = async (action: () => Promise<SharedModels>) => {
    setBusy(true); setError(''); setNotice('');
    try { apply(await action()); setNotice(t('sharedModels.success')); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t('sharedModels.error')); }
    finally { setBusy(false); }
  };
  const importExisting = (event: FormEvent) => {
    event.preventDefault();
    if (!current || !source || !window.confirm(t('sharedModels.confirm'))) return;
    void run(() => operationsApi.importSharedModels(source, current.version, reason.trim()));
  };
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!current || !window.confirm(t('sharedModels.confirm'))) return;
    void run(() => operationsApi.saveSharedModels({
      expected_version: current.version,
      providers: providers.map(provider => ({ ...provider, api_key: provider.api_key?.trim() || undefined })),
      defaults, reason: reason.trim(),
    }));
  };

  return <section id="shared-models" className="my-6 rounded-xl border border-slate-200 bg-white p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="font-semibold">{t('sharedModels.title')}</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{t('sharedModels.hint')}</p></div>
      <button className={button} disabled={busy} onClick={() => void load()}>{t('sharedModels.refresh')}</button>
    </div>
    <p className="mt-3 text-sm">{t(current?.enabled ? 'sharedModels.configured' : 'sharedModels.missing')}</p>
    {current?.enabled && <p className="mt-1 text-sm" role="status">{t(`sharedModels.sync${current.synchronization === 'synced' ? 'Complete' : current.synchronization === 'failed' ? 'Failed' : 'Pending'}`)}</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    {notice && <p role="status" className="mt-3 text-sm text-green-700">{notice}</p>}
    {current && !current.enabled && <form onSubmit={importExisting} className="mt-5 grid max-w-2xl gap-4">
      <h4 className="font-medium">{t('sharedModels.importTitle')}</h4>
      <p className="text-sm text-slate-500">{t('sharedModels.importHint')}</p>
      <label className="text-sm">{t('sharedModels.source')}
        <select required className={field} value={source} onChange={event => setSource(event.target.value)}>
          {projects.map(project => <option key={project.project_id} value={project.project_id}>{project.name} · {project.project_id}</option>)}
        </select>
      </label>
      <label className="text-sm">{t('sharedModels.reason')}
        <textarea required minLength={5} maxLength={500} className={field} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button className={button} disabled={busy || !source} type="submit">{t('sharedModels.import')}</button>
    </form>}
    {current?.enabled && <form onSubmit={save} className="mt-6 space-y-7">
      {providers.map((provider, index) => <article key={provider.id} className="rounded-xl border border-slate-200 p-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label className="text-sm">{t('sharedModels.name')}<input required maxLength={100} className={field} value={provider.name} onChange={event => updateProvider(index, { ...provider, name: event.target.value })} /></label>
          <label className="text-sm">{t('sharedModels.provider')}<input required maxLength={50} className={field} value={provider.provider} onChange={event => updateProvider(index, { ...provider, provider: event.target.value })} /></label>
          <label className="text-sm">{t('sharedModels.endpoint')}<input type="url" maxLength={255} className={field} value={provider.api_base_url ?? ''} onChange={event => updateProvider(index, { ...provider, api_base_url: event.target.value || null })} /></label>
          <label className="text-sm">{t('sharedModels.key')}<input type="password" autoComplete="new-password" className={field} value={provider.api_key ?? ''} onChange={event => updateProvider(index, { ...provider, api_key: event.target.value })} />
            <span className="text-xs text-slate-500">{t(current.providers.some(item => item.id === provider.id && item.has_api_key) ? 'sharedModels.keyKeep' : 'sharedModels.keyNew')}</span></label>
          <label className="flex items-center gap-2 self-end text-sm"><input type="checkbox" checked={provider.is_active} onChange={event => updateProvider(index, { ...provider, is_active: event.target.checked })} />{t('sharedModels.active')}</label>
        </div>
        <div className="mt-4 space-y-3">{provider.models.map((model, modelIndex) => <div key={`${provider.id}:${modelIndex}`} className="flex flex-wrap items-center gap-2">
          <label className="min-w-48 flex-1 text-sm">{t('sharedModels.model')}<input required maxLength={100} className={field} value={model.model_id} onChange={event => updateProvider(index, { ...provider, models: provider.models.map((item, position) => position === modelIndex ? { ...item, model_id: event.target.value } : item) })} /></label>
          <label className="min-w-32 text-sm">{t('sharedModels.purpose')}<select className={field} value={model.model_type} onChange={event => updateProvider(index, { ...provider, models: provider.models.map((item, position) => position === modelIndex ? { ...item, model_type: event.target.value as ModelPurpose } : item) })}>
            {purposes.map(purpose => <option key={purpose} value={purpose}>{t(`sharedModels.purposes.${purpose}`)}</option>)}
          </select></label>
          <button type="button" className={button} onClick={() => updateProvider(index, { ...provider, models: provider.models.filter((_, position) => position !== modelIndex) })}>{t('sharedModels.removeModel')}</button>
        </div>)}</div>
        <div className="mt-4 flex gap-2">
          <button type="button" className={button} onClick={() => updateProvider(index, { ...provider, models: [...provider.models, { model_id: '', model_type: 'chat', capabilities: null }] })}>{t('sharedModels.addModel')}</button>
          <button type="button" className={button} onClick={() => setProviders(previous => previous.filter((_, position) => position !== index))}>{t('sharedModels.removeProvider')}</button>
        </div>
      </article>)}
      <button type="button" className={button} onClick={addProvider}>{t('sharedModels.addProvider')}</button>
      <section><h4 className="font-medium">{t('sharedModels.defaults')}</h4>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{purposes.map(purpose => <label key={purpose} className="text-sm">{t(`sharedModels.purposes.${purpose}`)}
          <select required className={field} value={`${defaults[purpose].provider_id}:${defaults[purpose].model_id}`} onChange={event => {
            const [provider_id, ...modelParts] = event.target.value.split(':');
            setDefaults(previous => ({ ...previous, [purpose]: { provider_id, model_id: modelParts.join(':') } }));
          }}>
            <option value=":">{t('sharedModels.missing')}</option>
            {providers.filter(provider => provider.is_active).flatMap(provider => provider.models.filter(model => model.model_type === purpose && model.model_id).map(model =>
              <option key={`${provider.id}:${model.model_id}`} value={`${provider.id}:${model.model_id}`}>{provider.name} · {model.model_id}</option>
            ))}
          </select>
        </label>)}</div>
      </section>
      <label className="block max-w-2xl text-sm">{t('sharedModels.reason')}
        <textarea required minLength={5} maxLength={500} className={field} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button type="submit" disabled={busy} className={button}>{t('sharedModels.save')}</button>
    </form>}
  </section>;
}
