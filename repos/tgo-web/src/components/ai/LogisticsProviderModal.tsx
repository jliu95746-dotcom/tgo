import { useRef, useState } from 'react';
import { X, Truck, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { saveLogisticsProvider } from '@/services/logisticsProviderService';
import { logisticsApi } from '@/services/logisticsApi';
import { defaultLogisticsProvider, type LogisticsProviderConfig } from '@/types/logisticsProvider';
import type { AiToolResponse } from '@/types';

interface Props {
  tool: AiToolResponse | null;
  onClose: () => void;
  onSaved: (tool: AiToolResponse, linked: boolean) => void;
}

export default function LogisticsProviderModal({ tool, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const previous = tool?.config?.logistics_provider as LogisticsProviderConfig | undefined;
  const [form, setForm] = useState<LogisticsProviderConfig>({ ...defaultLogisticsProvider, ...previous, credential: '' });
  const [selectedKind, setSelectedKind] = useState(previous ? previous.provider_kind || 'custom' : '');
  const [endpoint, setEndpoint] = useState(previous ? tool?.endpoint || '' : '');
  const [fixedParams, setFixedParams] = useState(JSON.stringify(previous?.fixed_params || {}, null, 2));
  const [saved, setSaved] = useState(previous ? tool : null);
  const [dirty, setDirty] = useState(!previous);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [trackingNo, setTrackingNo] = useState('');
  const [carrierCode, setCarrierCode] = useState('');
  const [phone, setPhone] = useState('');
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const inputClass = 'w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm text-gray-900 dark:text-gray-100';

  function update<K extends keyof LogisticsProviderConfig>(field: K, value: LogisticsProviderConfig[K]) {
    setForm(current => ({ ...current, [field]: value }));
    setDirty(true);
    setNotice(null);
  }

  function selectProvider(kind: string) {
    setSelectedKind(kind);
    setForm({ ...defaultLogisticsProvider, provider_kind: kind as LogisticsProviderConfig['provider_kind'],
      provider_name: kind === 'custom' ? '' : t(`logisticsProvider.providers.${kind}`), credential: '', credential_configured: false });
    setEndpoint('');
    setFixedParams('{}');
    setCarrierCode('');
    setPhone('');
    setDirty(true);
    setNotice(null);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!user?.project_id || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setNotice(null);
    try {
      if (!selectedKind) return;
      if (selectedKind === 'custom') {
        const url = new URL(endpoint.trim());
        if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw new Error(t('logisticsProvider.invalidUrl'));
      }
      const parsed: unknown = JSON.parse(fixedParams);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || Object.values(parsed).some(v => typeof v !== 'string')) throw new Error(t('logisticsProvider.invalidParams'));
      const value = await saveLogisticsProvider(user.project_id, saved || tool, endpoint.trim(), { ...form, provider_name: form.provider_name.trim(), fixed_params: parsed as Record<string, string> });
      setSaved(value.tool);
      setForm({ ...defaultLogisticsProvider, ...value.tool.config?.logistics_provider as LogisticsProviderConfig, credential: '' });
      setDirty(!value.linked);
      setNotice({ error: !value.linked, text: t(value.linked ? 'logisticsProvider.saved' : 'logisticsProvider.linkFailed') });
      onSaved(value.tool, value.linked);
    } catch (error) {
      // Never show raw request/validation contents containing the entered key.
      const message = error instanceof SyntaxError ? t('logisticsProvider.invalidParams') : t('logisticsProvider.saveFailed');
      setNotice({ error: true, text: message });
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function test() {
    if (!saved || dirty || busyRef.current || !trackingNo.trim()) return;
    busyRef.current = true;
    setBusy(true);
    setNotice(null);
    try {
      const result = await logisticsApi.testTool(trackingNo.trim(), saved.id, { carrier_code: carrierCode.trim() || undefined, phone: phone.trim() || undefined });
      setNotice({ error: false, text: `${result.message}${result.preview ? `：${result.preview}` : ''}` });
    } catch {
      setNotice({ error: true, text: t('logisticsProvider.testFailed') });
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const textField = (key: 'provider_name' | 'auth_header' | 'tracking_param' | 'success_path' | 'success_value' | 'events_path' | 'time_field' | 'description_field' | 'carrier_path' | 'tracking_path', required = true) => (
    <label className="space-y-1 block" key={key}>
      <span className="text-sm">{t(`logisticsProvider.fields.${key}`)}{required ? ' *' : ''}</span>
      <input className={inputClass} required={required} value={form[key]} onChange={event => update(key, event.target.value)} />
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <section role="dialog" aria-modal="true" aria-labelledby="logistics-provider-title" className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-2xl bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 shadow-xl">
        <header className="flex items-center justify-between border-b border-gray-200 dark:border-gray-700 p-5">
          <h2 id="logistics-provider-title" className="flex items-center gap-2 text-lg font-semibold"><Truck className="h-5 w-5" />{t('logisticsProvider.title')}</h2>
          <button type="button" disabled={busy} onClick={onClose} aria-label={t('common.close')}><X className="h-5 w-5" /></button>
        </header>
        <form onSubmit={save} className="overflow-y-auto p-5 space-y-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">{t('logisticsProvider.sharedHint')}</p>
          {tool && !previous && !saved && <p className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-700 dark:text-amber-300">{t('logisticsProvider.migrateHint')}</p>}
          <fieldset disabled={busy} className="space-y-4 disabled:opacity-70">
            <label className="block space-y-1"><span className="text-sm">{t('logisticsProvider.provider')} *</span>
              <select required className={inputClass} value={selectedKind} onChange={event => selectProvider(event.target.value)}>
                <option value="" disabled>{t('logisticsProvider.choose')}</option>
                <option value="kuaidi100">{t('logisticsProvider.providers.kuaidi100')}</option>
                <option value="kdniao">{t('logisticsProvider.providers.kdniao')}</option>
                <option value="custom">{t('logisticsProvider.providers.custom')}</option>
              </select>
            </label>
            {selectedKind && selectedKind !== 'custom' && <>
              <p className="rounded-lg bg-blue-50 dark:bg-blue-950/30 p-3 text-sm text-blue-700 dark:text-blue-300">{t(`logisticsProvider.hints.${selectedKind}`)}</p>
              <label className="block space-y-1"><span className="text-sm">{t(`logisticsProvider.accounts.${selectedKind}`)} *</span><input required autoComplete="off" className={inputClass} value={form.account_id || ''} onChange={event => { update('account_id', event.target.value); update('credential_configured', false); }} /></label>
            </>}
            {selectedKind === 'custom' && <>
            {textField('provider_name')}
            <label className="block space-y-1"><span className="text-sm">{t('logisticsProvider.endpoint')} *</span><input type="url" required className={inputClass} placeholder="https://" value={endpoint} onChange={event => { setEndpoint(event.target.value); setDirty(true); }} /></label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="space-y-1"><span className="text-sm">{t('logisticsProvider.method')}</span><select className={inputClass} value={form.method} onChange={event => update('method', event.target.value as 'GET' | 'POST')}><option>GET</option><option>POST</option></select></label>
              {form.method === 'POST' && <label className="space-y-1"><span className="text-sm">{t('logisticsProvider.format')}</span><select className={inputClass} value={form.body_format} onChange={event => update('body_format', event.target.value as 'json' | 'form')}><option value="json">JSON</option><option value="form">Form</option></select></label>}
            </div>
            <label className="block space-y-1"><span className="text-sm">{t('logisticsProvider.auth')}</span><select className={inputClass} value={form.auth_type} onChange={event => update('auth_type', event.target.value as LogisticsProviderConfig['auth_type'])}><option value="appcode">AppCode</option><option value="bearer">Bearer Token</option><option value="header">{t('logisticsProvider.customHeader')}</option><option value="none">{t('logisticsProvider.noAuth')}</option></select></label>
            {form.auth_type === 'header' && textField('auth_header')}
            </>}
            {selectedKind && form.auth_type !== 'none' && <label className="block space-y-1"><span className="text-sm">{selectedKind === 'custom' ? t('logisticsProvider.credential') : t(`logisticsProvider.keys.${selectedKind}`)} *</span><input type="password" required={!form.credential_configured} autoComplete="new-password" className={inputClass} value={form.credential || ''} placeholder={form.credential_configured ? t('logisticsProvider.keepKey') : t('logisticsProvider.enterKey')} onChange={event => update('credential', event.target.value)} /><span className="block text-xs text-gray-500">{t('logisticsProvider.keyHint')}</span></label>}
            {selectedKind === 'custom' && <>
            {textField('tracking_param')}
            <details className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
              <summary className="cursor-pointer text-sm font-medium">{t('logisticsProvider.mapping')}</summary>
              <p className="my-3 text-xs text-gray-500">{t('logisticsProvider.mappingHint')}</p>
              <div className="grid gap-3 sm:grid-cols-2">{(['success_path', 'success_value', 'events_path', 'time_field', 'description_field'] as const).map(key => textField(key))}{textField('carrier_path', false)}{textField('tracking_path', false)}</div>
              <label className="mt-3 block space-y-1"><span className="text-sm">{t('logisticsProvider.fixedParams')}</span><textarea rows={3} className={`${inputClass} font-mono`} value={fixedParams} onChange={event => { setFixedParams(event.target.value); setDirty(true); }} /><span className="block text-xs text-gray-500">{t('logisticsProvider.paramsHint')}</span></label>
            </details>
            <p className="text-xs text-gray-500">{t('logisticsProvider.supportHint')}</p>
            </>}
          </fieldset>
          {notice && <p role="status" className={`rounded-lg p-3 text-sm ${notice.error ? 'bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300' : 'bg-green-50 text-green-700 dark:bg-green-950/30 dark:text-green-300'}`}>{notice.text}</p>}
          <div className="flex gap-3 justify-end"><button type="button" disabled={busy} onClick={onClose} className="px-4 py-2 text-sm">{t('common.close')}</button><button type="submit" disabled={busy || !dirty} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{busy && <Loader2 className="h-4 w-4 animate-spin" />}{t('logisticsProvider.save')}</button></div>
          <div className="border-t border-gray-200 dark:border-gray-700 pt-4 space-y-2">
            <p className="text-xs text-gray-500">{t('logisticsProvider.testHint')}</p>
            <div className="flex gap-2"><input aria-label={t('logisticsProvider.trackingNo')} className={inputClass} value={trackingNo} onChange={event => setTrackingNo(event.target.value)} placeholder={t('logisticsProvider.trackingNo')} /><button type="button" disabled={busy || dirty || !saved || !trackingNo.trim()} onClick={test} className="shrink-0 rounded-lg border border-blue-500 px-4 py-2 text-sm text-blue-500 disabled:opacity-50">{t('logisticsProvider.test')}</button></div>
            {selectedKind && selectedKind !== 'custom' && <details className="text-sm">
              <summary className="cursor-pointer">{t('logisticsProvider.extraQuery')}</summary>
              <p className="my-2 text-xs text-gray-500">{t('logisticsProvider.extraQueryHint')}</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label>{t('logisticsProvider.carrierCode')}<input disabled={busy} className={inputClass} maxLength={40} value={carrierCode} onChange={event => setCarrierCode(event.target.value)} /></label>
                <label>{t('logisticsProvider.phone')}<input disabled={busy} className={inputClass} maxLength={11} inputMode="numeric" autoComplete="off" value={phone} onChange={event => setPhone(event.target.value)} /></label>
              </div>
            </details>}
          </div>
        </form>
      </section>
    </div>
  );
}
