import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import { durableCountKeys, failureNoticeKeys, type DurableCounts, type WeComDurableStatus } from '../../types/wecomDurableStatus';

const needsAttention = (counts: DurableCounts) =>
  counts.terminal_failure > 0 || counts.stale_lease > 0;

export default function WeComDurableHealth() {
  const { t, i18n } = useTranslation();
  const [revision, setRevision] = useState(0);
  const [report, setReport] = useState<WeComDurableStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setBusy(true);
    setReport(null);
    setError('');
    operationsApi.wecomDurableStatus().then(data => {
      if (active) setReport(data);
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : t('opsWorkspace.error'));
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [revision, t]);

  const attention = report && (
    needsAttention(report.inbox)
    || needsAttention(report.media_jobs)
    || report.failure_notices.unknown > 0
    || report.failure_notices.needs_attention > 0
  );
  return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6" aria-busy={busy}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold">{t('opsWorkspace.wecomDurable.title')}</h2>
        <p className="mt-1 text-sm text-slate-600">{t('opsWorkspace.wecomDurable.hint')}</p>
      </div>
      <button className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40" disabled={busy} onClick={() => setRevision(value => value + 1)}>{t('opsWorkspace.refresh')}</button>
    </div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {report && <>
      <p role={attention ? 'alert' : 'status'} className={attention ? 'text-sm font-medium text-amber-800' : 'text-sm text-slate-600'}>
        {t(attention ? 'opsWorkspace.wecomDurable.attention' : 'opsWorkspace.wecomDurable.clear')}
        {' · '}{t('opsWorkspace.checkedAt', { time: new Date(report.checked_at).toLocaleString(i18n.language) })}
      </p>
      {(['inbox', 'media_jobs'] as const).map(group => <div key={group}>
        <h3 className="mb-2 text-sm font-medium text-slate-700">{t(`opsWorkspace.wecomDurable.groups.${group}`)}</h3>
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">{durableCountKeys.map(key => <div key={key} className="rounded-xl border border-slate-200 p-3">
          <dt className="text-xs text-slate-600">{t(`opsWorkspace.wecomDurable.counts.${key}`)}</dt>
          <dd className={`mt-1 text-xl font-semibold ${(key === 'terminal_failure' || key === 'stale_lease') && report[group][key] > 0 ? 'text-amber-800' : 'text-slate-900'}`}>{report[group][key]}</dd>
        </div>)}</dl>
      </div>)}
      <div>
        <h3 className="mb-2 text-sm font-medium text-slate-700">{t('opsWorkspace.wecomDurable.groups.failure_notices')}</h3>
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{failureNoticeKeys.map(key => <div key={key} className="rounded-xl border border-slate-200 p-3">
          <dt className="text-xs text-slate-600">{t(`opsWorkspace.wecomDurable.notices.${key}`)}</dt>
          <dd className={`mt-1 text-xl font-semibold ${(key === 'unknown' || key === 'needs_attention') && report.failure_notices[key] > 0 ? 'text-amber-800' : 'text-slate-900'}`}>{report.failure_notices[key]}</dd>
        </div>)}</dl>
        {(report.failure_notices.unknown > 0 || report.failure_notices.needs_attention > 0) && <p className="mt-2 text-sm text-amber-800">{t('opsWorkspace.wecomDurable.noticeAction')}</p>}
      </div>
    </>}
  </section>;
}
