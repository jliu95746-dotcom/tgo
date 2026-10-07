import { useEffect, useState } from 'react';

import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { CommercialReadiness as Readiness } from '../../types/operationsTasks';

export default function CommercialReadiness() {
  const { t } = useTranslation();
  const [data, setData] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setBusy(true); setError('');
    void operationsApi.commercialReadiness().then(value => { if (active) { setData(value); } })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error')); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [t]);
  const text = (key: string) => t(`billingSupport.${key}`);
  const load = async () => {
    setBusy(true); setError('');
    try { setData(await operationsApi.commercialReadiness()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : text('error')); }
    finally { setBusy(false); }
  };
  return <section className="my-6 space-y-4 rounded-xl border border-slate-200 p-5">
    <h2 className="text-xl font-semibold">{text('readinessTitle')}</h2>
    <p className="text-sm leading-6 text-slate-500">{text('readinessHint')}</p>
    <a href="/launch-guide.html" className="inline-flex rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white">{t('launchGuide.title')}</a>
    <button className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40" disabled={busy} onClick={() => void load()}>{text('checkConfig')}</button>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {data && <><dl className="grid gap-3 sm:grid-cols-2">{data.checks.map(item => <div key={item.code} className="rounded-lg bg-slate-50 p-3 text-sm"><dt>{text(`readiness.${item.code}`)}</dt><dd className={item.configured ? 'mt-1 text-green-800' : 'mt-1 text-amber-800'}>{text(item.configured ? 'configured' : 'missingConfig')}</dd></div>)}</dl><p className="text-sm">{text('billingSwitch')}: {text(data.billing_enabled ? 'enabled' : 'disabled')} · {text('purchaseSwitch')}: {text(data.purchases_enabled ? 'enabled' : 'disabled')} · {text('registrationSwitch')}: {text(data.registration_enabled ? 'enabled' : 'disabled')}</p></>}
  </section>;
}
