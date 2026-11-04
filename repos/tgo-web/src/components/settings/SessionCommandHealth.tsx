import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type {
  SessionCommandAlertItem, SessionCommandAlertReport,
} from '../../types/sessionCommandMonitoring';

const pageSize = 50;

export default function SessionCommandHealth() {
  const { t, i18n } = useTranslation();
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const [report, setReport] = useState<SessionCommandAlertReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<SessionCommandAlertItem | null>(null);
  const [reason, setReason] = useState('');
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const interval = window.setInterval(() => setRevision(value => value + 1), 60_000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    let active = true;
    setBusy(true);
    setReport(null);
    setError('');
    operationsApi.sessionCommandAlerts(offset, pageSize).then(data => {
      if (active) setReport(data);
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : t('opsWorkspace.error'));
    }).finally(() => {
      if (active) setBusy(false);
    });
    return () => { active = false; };
  }, [revision, offset, t]);

  useEffect(() => {
    if (selected) dialog.current?.showModal();
    else dialog.current?.close();
  }, [selected]);

  const reconcile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || actionBusy || (selected.notification_status !== 'unknown' && selected.notification_status !== 'sending')) return;
    setActionBusy(true);
    setActionError('');
    try {
      await operationsApi.reconcileSessionNotice(selected.operation_id, {
        expected_status: selected.notification_status,
        reason: reason.trim(),
      });
      setSelected(null);
      setRevision(value => value + 1);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : t('opsWorkspace.error'));
    } finally {
      setActionBusy(false);
    }
  };

  const canReconcile = (item: SessionCommandAlertItem, staleAfterSeconds: number) =>
    (item.notification_status === 'unknown' || item.notification_status === 'sending')
    && Date.now() - Date.parse(item.notification_updated_at) >= staleAfterSeconds * 1000;

  const label = (state: string) => t(`opsWorkspace.sessionCommands.states.${state}`, { defaultValue: state });
  return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6" aria-busy={busy}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold">{t('opsWorkspace.sessionCommands.title')}</h2>
        <p className="mt-1 text-sm text-slate-600">{t('opsWorkspace.sessionCommands.hint')}</p>
      </div>
      <button className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40" disabled={busy} onClick={() => setRevision(value => value + 1)}>{t('opsWorkspace.refresh')}</button>
    </div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {report && <>
      <p role={report.total ? 'alert' : 'status'} className={report.total ? 'text-sm font-medium text-amber-800' : 'text-sm text-slate-600'}>
        {report.total ? t('opsWorkspace.sessionCommands.attention', { count: report.total }) : t('opsWorkspace.sessionCommands.clear')}
        {' · '}{t('opsWorkspace.checkedAt', { time: new Date(report.checked_at).toLocaleString(i18n.language) })}
      </p>
      {report.items.length > 0 && <div className="overflow-x-auto">
        <table className="w-full min-w-[800px] text-left text-sm">
          <thead className="border-b text-slate-500"><tr>
            <th scope="col" className="py-3 pr-4">{t('opsWorkspace.sessionCommands.company')}</th>
            <th scope="col" className="py-3 pr-4">{t('opsWorkspace.sessionCommands.operation')}</th>
            <th scope="col" className="py-3 pr-4">{t('opsWorkspace.sessionCommands.kind')}</th>
            <th scope="col" className="py-3 pr-4">{t('opsWorkspace.sessionCommands.notice')}</th>
            <th scope="col" className="py-3 pr-4">{t('opsWorkspace.sessionCommands.effects')}</th>
            <th scope="col" className="py-3">{t('opsWorkspace.sessionCommands.error')}</th>
            <th scope="col" className="py-3">{t('opsWorkspace.sessionCommands.action')}</th>
          </tr></thead>
          <tbody>{report.items.map(item => <tr key={item.operation_id} className="border-b border-slate-100 align-top">
            <td className="py-3 pr-4"><Link to={`/ops/companies/${item.project_id}`} className="break-all text-indigo-600 hover:underline">{item.project_id}</Link></td>
            <td className="py-3 pr-4 font-mono text-xs break-all">{item.operation_id}</td>
            <td className="py-3 pr-4">{label(item.kind)}</td>
            <td className="py-3 pr-4">{label(item.notification_status)} ({item.notification_attempts})</td>
            <td className="py-3 pr-4">{label(item.effects_status)} ({item.effects_attempts})</td>
            <td className="py-3 font-mono text-xs break-all">{[item.notification_error, item.effects_error].filter(Boolean).join(' / ') || '—'}</td>
            <td className="py-3">{canReconcile(item, report.stale_after_seconds) && <button className="whitespace-nowrap rounded-lg border px-3 py-2 text-xs text-indigo-700 disabled:opacity-40" disabled={actionBusy} onClick={() => { setActionError(''); setReason(''); setSelected(item); }}>{t('opsWorkspace.sessionCommands.reconcileNotice')}</button>}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      {report.total > pageSize && <div className="flex items-center gap-3 text-sm">
        <button className="rounded-lg border px-3 py-2 disabled:opacity-40" disabled={busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>{t('opsWorkspace.previous')}</button>
        <span>{t('opsWorkspace.page', { page: Math.floor(report.offset / pageSize) + 1 })}</span>
        <button className="rounded-lg border px-3 py-2 disabled:opacity-40" disabled={busy || offset + report.items.length >= report.total} onClick={() => setOffset(value => value + pageSize)}>{t('opsWorkspace.next')}</button>
      </div>}
    </>}
    <dialog ref={dialog} className="w-[min(92vw,480px)] rounded-2xl p-6 backdrop:bg-black/40" onCancel={event => { if (actionBusy) event.preventDefault(); else setSelected(null); }}>
      <form onSubmit={event => void reconcile(event)} className="space-y-4">
        <h3 className="text-xl font-semibold">{t('opsWorkspace.sessionCommands.reconcileNotice')}</h3>
        <p className="text-sm text-slate-600">{t('opsWorkspace.sessionCommands.reconcileHint')}</p>
        <p className="break-all font-mono text-xs">{selected?.operation_id}</p>
        <p className="text-sm">{t('opsWorkspace.sessionCommands.notice')}: {selected && label(selected.notification_status)}</p>
        <label className="block text-sm">{t('opsWorkspace.reason')}<textarea required minLength={5} maxLength={500} value={reason} onChange={event => setReason(event.target.value)} className="mt-2 w-full rounded-lg border p-3" /></label>
        {actionError && <p role="alert" className="text-sm text-red-700">{actionError}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40" disabled={actionBusy} onClick={() => setSelected(null)}>{t('opsWorkspace.cancel')}</button>
          <button className="rounded-lg border px-4 py-2 text-sm text-indigo-700 disabled:opacity-40" disabled={actionBusy || reason.trim().length < 5}>{t('opsWorkspace.sessionCommands.confirmReconcile')}</button>
        </div>
      </form>
    </dialog>
  </section>;
}
