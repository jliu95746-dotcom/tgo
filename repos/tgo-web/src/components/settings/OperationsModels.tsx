import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { PlatformModelDefinition, PlatformModelPolicy } from '../../types/platformModels';

const button = 'rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-40';
const input = 'mt-2 block w-full rounded-lg border border-slate-300 px-3 py-2';
const blank: PlatformModelDefinition = { model: '', provider_kind: 'openai', api_base_url: null, vendor: null, active: true, input_fen_per_million: null, output_fen_per_million: null };

export default function OperationsModels() {
  const { t } = useTranslation();
  const [policy, setPolicy] = useState<PlatformModelPolicy | null>(null);
  const [form, setForm] = useState<PlatformModelDefinition>(blank);
  const [key, setKey] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sharedManaged, setSharedManaged] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.location.hash === '#platform-model') section.current?.scrollIntoView();
  }, []);
  useEffect(() => {
    void operationsApi.sharedModels().then(value => setSharedManaged(value.enabled)).catch(() => {});
  }, []);
  const load = async () => {
    setBusy(true); setError(''); setKey('');
    try { const value = await operationsApi.modelPolicy(); setPolicy(value); setForm(value.definition ?? blank); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
    finally { setBusy(false); }
  };
  const review = (event: FormEvent) => { event.preventDefault(); if (reason.trim().length >= 5) dialog.current?.showModal(); };
  const save = async () => {
    if (!policy) return;
    setBusy(true); setError('');
    try {
      const value = await operationsApi.saveModelPolicy({ ...form, model: form.model.trim(), expected_version: policy.version, reason: reason.trim(), ...(key ? { api_key: key } : {}) });
      setPolicy(value); setForm(value.definition ?? blank); setReason('');
    } catch (caught) { setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
    finally { setKey(''); setBusy(false); dialog.current?.close(); }
  };
  if (sharedManaged) return null;
  return <section ref={section} id="platform-model" className="my-6 scroll-mt-6 rounded-xl border border-slate-200 bg-white p-5">
    <h3 className="font-semibold">{t('billingSupport.platformModel')}</h3>
    <p className="my-3 text-sm leading-6 text-slate-500">{t('billingSupport.platformModelHint')}</p>
    <button className={button} disabled={busy} onClick={() => void load()}>{t('billingSupport.refresh')}</button>
    {error && <p role="alert" className="my-3 text-sm text-red-700">{error}</p>}
    {policy && <form onSubmit={review} className="mt-4 grid max-w-3xl gap-4 sm:grid-cols-2">
      <label className="text-sm">{t('billingSupport.model')}<input className={input} required maxLength={150} value={form.model} onChange={event => setForm({ ...form, model: event.target.value })} /></label>
      <label className="text-sm">{t('billingSupport.providerKind')}<select className={input} value={form.provider_kind} onChange={event => setForm({ ...form, provider_kind: event.target.value as PlatformModelDefinition['provider_kind'] })}><option value="openai">OpenAI</option><option value="openai_compatible">{t('billingSupport.compatibleProvider')}</option><option value="anthropic">Anthropic</option><option value="google">Google</option></select></label>
      <label className="text-sm">{t('billingSupport.modelEndpoint')}<input className={input} type="url" required={form.provider_kind === 'openai_compatible'} maxLength={255} value={form.api_base_url ?? ''} onChange={event => setForm({ ...form, api_base_url: event.target.value || null })} /></label>
      <label className="text-sm">{t('billingSupport.modelVendor')}<input className={input} maxLength={40} value={form.vendor ?? ''} onChange={event => setForm({ ...form, vendor: event.target.value || null })} /></label>
      <label className="text-sm sm:col-span-2">API Key<input className={input} type="password" autoComplete="new-password" maxLength={4096} required={form.active && !policy.has_api_key} value={key} onChange={event => setKey(event.target.value)} /><span className="mt-1 block text-xs text-slate-500">{t(policy.has_api_key ? 'billingSupport.modelKeyKeep' : 'billingSupport.modelKeyNew')}</span></label>
      {(['input_fen_per_million', 'output_fen_per_million'] as const).map(field => <label key={field} className="text-sm">{t(`billingSupport.${field}`)}<input className={input} type="number" min={0} step="0.000001" value={form[field] ?? ''} onChange={event => setForm({ ...form, [field]: event.target.value || null })} /></label>)}
      <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={form.active} onChange={event => setForm({ ...form, active: event.target.checked })} />{t('billingSupport.modelActive')}</label>
      <label className="text-sm sm:col-span-2">{t('billingSupport.reason')}<textarea className={input} required minLength={5} maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button className={button} disabled={busy} type="submit">{t('billingSupport.reviewModel')}</button>
    </form>}
    <dialog ref={dialog} onCancel={() => setKey('')} className="w-[min(92vw,32rem)] rounded-xl p-6 backdrop:bg-black/40">
      <h4 className="font-semibold">{t('billingSupport.reviewModel')}</h4>
      <p className="my-4 text-sm leading-6">{t('billingSupport.modelChangeScope')}</p>
      <p className="break-all text-sm">{form.model} · {form.provider_kind}</p>
      <p className="my-3 break-all text-sm">{form.api_base_url || t('billingSupport.providerDefaultEndpoint')}</p>
      <p className="my-3 text-sm">{t(key ? 'billingSupport.modelKeyReplaced' : 'billingSupport.modelKeyUnchanged')}</p>
      <p className="my-3 text-sm">{t(form.active ? 'billingSupport.enabled' : 'billingSupport.disabled')}</p>
      <div className="flex gap-3"><button className={button} disabled={busy} onClick={() => { setKey(''); dialog.current?.close(); }}>{t('billingSupport.cancel')}</button><button className={button} disabled={busy} onClick={() => void save()}>{t('billingSupport.confirmChange')}</button></div>
    </dialog>
  </section>;
}
