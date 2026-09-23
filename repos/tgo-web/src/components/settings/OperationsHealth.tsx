import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import { commercialHealthMetrics, type CommercialHealth } from '../../types/commercialHealth';

export default function OperationsHealth() {
  const { t, i18n } = useTranslation();
  const [revision, setRevision] = useState(0);
  const [report, setReport] = useState<CommercialHealth | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!revision) return;
    let active = true;
    setBusy(true);
    setReport(null);
    setError('');
    operationsApi.commercialHealth().then(data => {
      if (active) setReport(data);
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error'));
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [revision, t]);
  return <section className="mt-8 space-y-4 border-t pt-8" aria-busy={busy}>
    <h2 className="text-2xl font-semibold">{t('billingSupport.healthTitle')}</h2>
    <p className="text-sm text-slate-600">{t('billingSupport.healthHint')}</p>
    <button className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40" disabled={busy} onClick={() => setRevision(value => value + 1)}>{t('billingSupport.refresh')}</button>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {report && <>
      <p role="status" className={report.status === 'attention' ? 'text-amber-800' : 'text-slate-700'}>{t(`billingSupport.healthState.${report.status}`)} · {new Date(report.checked_at).toLocaleString(i18n.language)}</p>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{commercialHealthMetrics.map(metric => <div key={metric} className="rounded-xl border p-4"><dt className="text-sm text-slate-600">{t(`billingSupport.healthMetrics.${metric}`)}</dt><dd className="mt-2 text-2xl font-semibold">{report.counts[metric] ?? '—'}</dd></div>)}</dl>
    </>}
  </section>;
}
