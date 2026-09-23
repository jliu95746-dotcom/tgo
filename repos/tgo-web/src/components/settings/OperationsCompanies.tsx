import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { OperationsCompany } from '../../types/billingSupport';

const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-40';
const input = 'mt-2 w-full rounded-lg border border-slate-300 px-3 py-2';
type Selection = { company: OperationsCompany; kind: 'credits' | 'state'; requestId: string };

export default function OperationsCompanies() {
  const { t, i18n } = useTranslation();
  const text = (key: string) => t(`billingSupport.${key}`);
  const [opened, setOpened] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [companies, setCompanies] = useState<OperationsCompany[]>([]);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [reason, setReason] = useState('');
  const [delta, setDelta] = useState('');
  const [expiry, setExpiry] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!opened) return;
    let active = true;
    setBusy(true); setError('');
    void operationsApi.companies(offset).then(data => { if (active) setCompanies(data); })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error')); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [opened, offset, revision, t]);
  useEffect(() => { if (selected) dialog.current?.showModal(); else dialog.current?.close(); }, [selected]);
  const open = (company: OperationsCompany, kind: Selection['kind']) => {
    setSelected({ company, kind, requestId: crypto.randomUUID() }); setReason(''); setDelta(''); setExpiry(''); setError('');
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!selected || busy) return;
    setBusy(true); setError('');
    try {
      if (selected.kind === 'credits') {
        const count = Number(delta);
        if (!/^-?\d+$/.test(delta) || !Number.isSafeInteger(count) || !count) throw new Error(text('invalid'));
        await operationsApi.adjustCredits(selected.company.id, { request_id: selected.requestId, delta: count,
          expires_at: count > 0 ? new Date(expiry).toISOString() : undefined, reason: reason.trim() });
      } else {
        await operationsApi.companyState(selected.company.id, { action: selected.company.status === 'suspended' ? 'restore' : 'suspend', reason: reason.trim() });
      }
      setSelected(null); setRevision(value => value + 1);
    } catch (caught) { setError(caught instanceof Error ? caught.message : text('error')); }
    finally { setBusy(false); }
  };
  return <section className="mt-8 space-y-4 border-t border-slate-200 pt-7">
    <h2 className="text-2xl font-semibold">{text('companies')}</h2>
    {!opened ? <button className={button} onClick={() => setOpened(true)}>{text('companyOpen')}</button> : <>
      <button className={button} disabled={busy} onClick={() => setRevision(value => value + 1)}>{text('refresh')}</button>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="grid gap-4 lg:grid-cols-2">{companies.map(company => <article key={company.id} className="rounded-xl border bg-white p-5">
        <h3 className="font-semibold">{company.name}</h3><p className="mt-1 break-all font-mono text-xs text-slate-500">{company.id}</p>
        <p className="mt-3 text-sm">{company.plan_name ?? t('billing.current')} · {t(`billing.${company.status}`)}</p>
        <p className="mt-2 text-sm">{t('billing.seatUsage', { used: company.used, reserved: company.reserved, limit: company.seats ?? '—' })}</p>
        <p className="mt-2 text-sm">{t('billing.ai')}: {company.ai_remaining}</p>
        <p className="mt-2 text-sm">{text('expiry')}: {company.expires_at ? new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(company.expires_at)) : '—'}</p>
        {!!company.order_exceptions && <p className="mt-2 text-sm text-amber-800">{text('exceptions')}: {company.order_exceptions}</p>}
        {company.status !== 'legacy' && <div className="mt-4 flex flex-wrap gap-2"><button className={button} disabled={busy} onClick={() => open(company, 'credits')}>{text('adjust')}</button><button className={button} disabled={busy} onClick={() => open(company, 'state')}>{text('stateChange')}</button></div>}
      </article>)}</div>
      {!companies.length && !busy && <p>{text('empty')}</p>}
      <div className="flex justify-end gap-2"><button className={button} disabled={busy || !offset} onClick={() => setOffset(Math.max(0, offset - 20))}>{text('previous')}</button><button className={button} disabled={busy || companies.length < 20} onClick={() => setOffset(offset + 20)}>{text('next')}</button></div>
    </>}
    <dialog ref={dialog} onCancel={event => { event.preventDefault(); if (!busy) setSelected(null); }} aria-labelledby="company-action-title" className="m-auto w-[min(92vw,32rem)] rounded-2xl bg-white p-7 backdrop:bg-black/40">
      <h2 id="company-action-title" className="text-xl font-semibold">{selected?.company.name}</h2>
      <form onSubmit={event => void submit(event)} className="mt-5 space-y-4">
        {selected?.kind === 'credits' ? <><p className="text-sm leading-6 text-slate-500">{text('adjustHint')}</p><label className="block text-sm">{text('delta')}<input required type="number" min={-100000000} max={100000000} step={1} className={input} value={delta} onChange={event => setDelta(event.target.value)} /></label>{Number(delta) > 0 && <label className="block text-sm">{text('expiry')}<input required type="datetime-local" className={input} value={expiry} onChange={event => setExpiry(event.target.value)} /></label>}</> : <p>{text(selected?.company.status === 'suspended' ? 'restore' : 'pauseDirect')}</p>}
        <label className="block text-sm">{text('reason')}<textarea required minLength={5} maxLength={500} className={input} value={reason} onChange={event => setReason(event.target.value)} /></label>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        <div className="flex gap-2"><button type="submit" className={button} disabled={busy}>{text('confirmChange')}</button><button type="button" className={button} disabled={busy} onClick={() => setSelected(null)}>{text('cancel')}</button></div>
      </form>
    </dialog>
  </section>;
}
