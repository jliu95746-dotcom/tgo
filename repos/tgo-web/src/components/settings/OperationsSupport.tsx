import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { BillingOrder } from '../../types/billing';
import type { InvoiceRequest, QuotaReply, Reconciliation, Refund } from '../../types/billingSupport';
import OperationsSupportDialog, { type SupportSelection } from './OperationsSupportDialog';

const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-40';
type Tab = 'orders' | 'refunds' | 'invoices' | 'reconciliation' | 'quotaReview';

export default function OperationsSupport({ initialTab = 'orders' }: { initialTab?: 'orders' | 'refunds' | 'invoices' | 'reconciliation' | 'quotaReview' }) {
  const { t, i18n } = useTranslation();
  const text = (key: string) => t(`billingSupport.${key}`);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [opened, setOpened] = useState(true);
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const [orders, setOrders] = useState<BillingOrder[]>([]);
  const [refunds, setRefunds] = useState<Refund[]>([]);
  const [invoices, setInvoices] = useState<InvoiceRequest[]>([]);
  const [reports, setReports] = useState<Reconciliation[]>([]);
  const [reviews, setReviews] = useState<QuotaReply[]>([]);
  const [selected, setSelected] = useState<SupportSelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const money = (value: number) => new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'CNY' }).format(value / 100);
  const state = (value: string) => t(`billingSupport.status.${value.toLowerCase()}`, { defaultValue: value });
  useEffect(() => {
    if (!opened) return;
    let active = true;
    setBusy(true); setError(''); setOrders([]); setRefunds([]); setInvoices([]); setReports([]); setReviews([]);
    const load = async () => {
      try {
        if (tab === 'orders') { const data = await operationsApi.orders(offset); if (active) setOrders(data); }
        else if (tab === 'refunds') { const data = await operationsApi.refunds(offset); if (active) setRefunds(data); }
        else if (tab === 'invoices') { const data = await operationsApi.invoices(offset); if (active) setInvoices(data); }
        else if (tab === 'reconciliation') { const data = await operationsApi.reconciliations(offset); if (active) setReports(data); }
        else { const data = await operationsApi.quotaReviews(offset); if (active) setReviews(data); }
      } catch (caught) { if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
      finally { if (active) setBusy(false); }
    };
    void load(); return () => { active = false; };
  }, [opened, tab, offset, revision, t]);
  const cancelRefund = async (id: string) => {
    setBusy(true); setError('');
    try { await operationsApi.cancelRefund(id); setRevision(value => value + 1); }
    catch (caught) { setError(caught instanceof Error ? caught.message : text('error')); }
    finally { setBusy(false); }
  };
  const count = tab === 'orders' ? orders.length : tab === 'refunds' ? refunds.length : tab === 'invoices' ? invoices.length : tab === 'reconciliation' ? reports.length : reviews.length;
  return <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6">
    <h2 className="text-2xl font-semibold">{text('title')}</h2>
    {!opened ? <button className={button} onClick={() => setOpened(true)}>{text('open')}</button> : <>
      <div className="flex flex-wrap gap-2">{(['orders', 'refunds', 'invoices', 'reconciliation', 'quotaReview'] as const).map(item => <button className={button} key={item} disabled={busy} aria-pressed={tab === item} onClick={() => { setTab(item); setOffset(0); }}>{item === 'orders' ? t('billing.orders') : text(item)}</button>)}<button className={button} disabled={busy} onClick={() => setRevision(value => value + 1)}>{text('refresh')}</button></div>
      {error && <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      <div className="space-y-3">
        {tab === 'orders' && orders.map(order => <article key={order.id} className="rounded-xl border bg-white p-5 text-sm">
          <p className="break-all font-mono">{order.number}</p><p className="my-2 text-xs text-slate-500">{text('company')}: {order.project_id}</p>
          <p>{money(order.amount)} · {text('refunded')}: {money(order.refunded_amount)} · {state(order.payment_status)} / {state(order.fulfillment_status)}</p>
          {order.transaction_id && <p className="mt-2 break-all text-xs">{text('transaction')}: {order.transaction_id}</p>}
          {order.failure_code && <p className="mt-2 text-amber-800">{text('failure')}: {order.failure_code}</p>}
          {order.payment_status === 'paid' && order.transaction_id && order.amount > order.refunded_amount && <button className={`${button} mt-3`} disabled={busy} onClick={() => setSelected({ kind: 'refund', order })}>{text('refundPreview')}</button>}
        </article>)}
        {tab === 'refunds' && refunds.map(refund => <article key={refund.id} className="rounded-xl border bg-white p-5 text-sm">
          <p className="break-all font-mono">{refund.number}</p><p className="my-2">{money(refund.amount)} · {state(refund.status)}</p><p className="text-xs text-slate-500">{text('company')}: {refund.project_id}</p><p className="my-2">{refund.reason}</p><p>{text(refund.disposition.action)}</p>
          {refund.status === 'draft' && <div className="mt-3 flex gap-2"><button className={button} disabled={busy} onClick={() => setSelected({ kind: 'preview', refund })}>{text('previewTitle')}</button><button className={button} disabled={busy} onClick={() => void cancelRefund(refund.id)}>{text('cancel')}</button></div>}
        </article>)}
        {tab === 'invoices' && invoices.map(invoice => <article key={invoice.id} className="rounded-xl border bg-white p-5 text-sm">
          <h3 className="font-semibold">{invoice.title}</h3><p className="my-2">{invoice.tax_number} · {invoice.email}</p><p className="break-all text-xs text-slate-500">{text('order')}: {invoice.order_id} · {text('company')}: {invoice.project_id}</p><p className="my-2">{state(invoice.status)} {invoice.invoice_number}</p>
          {invoice.status === 'requested' && <button className={button} disabled={busy} onClick={() => setSelected({ kind: 'invoice', invoice })}>{text('invoiceProcess')}</button>}
        </article>)}
        {tab === 'reconciliation' && reports.map(report => <article key={report.bill_date} className="rounded-xl border bg-white p-5 text-sm"><h3 className="font-semibold">{report.bill_date} · {state(report.status)}</h3><p className="my-2">{text('entries')}: {report.entry_count} · {text('issues')}: {report.issue_count}</p>
          {!!report.issues.length && <details><summary className="cursor-pointer">{text('details')}</summary><ul className="mt-3 space-y-2">{report.issues.map((issue, index) => <li key={`${issue.code}-${index}`} className="break-all rounded bg-amber-50 p-3 font-mono text-xs">{issue.code} · {issue.order_number} {issue.project_id}</li>)}</ul></details>}
        </article>)}
        {tab === 'quotaReview' && reviews.map(reply => <article key={reply.id} className="rounded-xl border bg-white p-5 text-sm"><p className="break-all font-mono text-xs">{reply.round_key}</p><p className="my-2 text-xs text-slate-500">{text('company')}: {reply.project_id}</p><button className={button} disabled={busy} onClick={() => setSelected({ kind: 'quota', reply })}>{text('quotaReview')}</button></article>)}
        {!count && !busy && <p className="text-sm text-slate-500">{text('empty')}</p>}
      </div>
      <div className="flex justify-end gap-2"><button className={button} disabled={busy || !offset} onClick={() => setOffset(Math.max(0, offset - 100))}>{text('previous')}</button><button className={button} disabled={busy || count < 100} onClick={() => setOffset(offset + 100)}>{text('next')}</button></div>
    </>}
    {selected && <OperationsSupportDialog selection={selected} onClose={() => setSelected(null)} onComplete={() => { setSelected(null); setRevision(value => value + 1); }} />}
  </section>;
}
