import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { billingApi } from '../../services/billingApi';
import type { BillingOrder } from '../../types/billing';
import type { InvoiceRequest, QuotaBatch, QuotaReply } from '../../types/billingSupport';

const button = 'rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-40';
const input = 'mt-2 w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2';

export default function BillingSupport({ admin, orders }: { admin: boolean; orders: BillingOrder[] }) {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<'batches' | 'replies' | 'invoices'>('batches');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [batches, setBatches] = useState<QuotaBatch[]>([]);
  const [replies, setReplies] = useState<QuotaReply[]>([]);
  const [invoices, setInvoices] = useState<InvoiceRequest[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [orderId, setOrderId] = useState('');
  const [title, setTitle] = useState('');
  const [tax, setTax] = useState('');
  const [email, setEmail] = useState('');
  const text = (key: string) => t(`billingSupport.${key}`);
  const date = (value: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  useEffect(() => {
    let active = true;
    setBusy(true); setError(''); setBatches([]); setReplies([]); setInvoices([]);
    const load = async () => {
      try {
        if (tab === 'batches') { const data = await billingApi.batches(offset); if (active) setBatches(data); }
        else if (admin && tab === 'replies') { const data = await billingApi.replies(offset); if (active) setReplies(data); }
        else if (admin) { const data = await billingApi.invoices(offset); if (active) setInvoices(data); }
      } catch (caught) { if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
      finally { if (active) setBusy(false); }
    };
    void load();
    return () => { active = false; };
  }, [admin, tab, offset, revision, t]);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await billingApi.requestInvoice({ order_id: orderId, title: title.trim(), tax_number: tax.trim(), email: email.trim() });
      setOrderId(''); setTitle(''); setTax(''); setEmail(''); setRevision(value => value + 1);
    } catch (caught) { setError(caught instanceof Error ? caught.message : text('error')); }
    finally { setBusy(false); }
  };
  const count = tab === 'batches' ? batches.length : tab === 'replies' ? replies.length : invoices.length;
  return <section className="space-y-4 border-t border-slate-200 pt-6">
    <div className="flex flex-wrap gap-3">{(['batches', ...(admin ? ['replies', 'invoices'] as const : [])] as const).map(item => <button className={button} key={item} aria-pressed={tab === item} onClick={() => { setTab(item); setOffset(0); }}>{text(item)}</button>)}
      <button className={button} disabled={busy} onClick={() => setRevision(value => value + 1)}>{text('refresh')}</button></div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {tab === 'invoices' && admin && <form onSubmit={event => void submit(event)} className="grid gap-4 rounded-xl border p-5 sm:grid-cols-2">
      <p className="text-sm text-slate-500 sm:col-span-2">{text('invoiceHint')}</p>
      <label className="text-sm">{text('order')}<select required className={input} value={orderId} onChange={event => setOrderId(event.target.value)}><option value="">—</option>{orders.filter(order => order.payment_status === 'paid' && order.amount > order.refunded_amount).map(order => <option key={order.id} value={order.id}>{order.number}</option>)}</select></label>
      <label className="text-sm">{text('invoiceTitle')}<input required minLength={2} maxLength={200} className={input} value={title} onChange={event => setTitle(event.target.value)} /></label>
      <label className="text-sm">{text('tax')}<input required minLength={8} maxLength={40} pattern="[A-Za-z0-9]+" className={input} value={tax} onChange={event => setTax(event.target.value)} /></label>
      <label className="text-sm">{text('email')}<input required type="email" maxLength={254} className={input} value={email} onChange={event => setEmail(event.target.value)} /></label>
      <div><button type="submit" className={button} disabled={busy}>{text('applyInvoice')}</button></div>
    </form>}
    <div className="overflow-x-auto rounded-xl border"><table className="w-full text-left text-sm"><thead><tr>
      {(tab === 'batches' ? ['kind', 'granted', 'remaining', 'expiry'] : tab === 'replies' ? ['round', 'state', 'created'] : ['order', 'invoiceTitle', 'state', 'invoiceNumber']).map(key => <th key={key} className="p-3">{text(key)}</th>)}
    </tr></thead><tbody>
      {tab === 'batches' && batches.map(row => <tr className="border-t" key={row.id}><td className="p-3">{t(`billingSupport.creditKind.${row.kind}`, { defaultValue: row.kind })}</td><td className="p-3">{row.amount}</td><td className="p-3">{row.remaining}</td><td className="p-3">{date(row.expires_at)}</td></tr>)}
      {tab === 'replies' && replies.map(row => <tr className="border-t" key={row.id}><td className="max-w-64 break-all p-3 font-mono text-xs">{row.round_key}</td><td className="p-3">{text(`status.${row.status}`)}</td><td className="p-3">{date(row.created_at)}</td></tr>)}
      {tab === 'invoices' && invoices.map(row => <tr className="border-t" key={row.id}><td className="p-3 font-mono text-xs">{orders.find(order => order.id === row.order_id)?.number ?? row.order_id}</td><td className="p-3">{row.title}</td><td className="p-3">{text(`status.${row.status}`)}</td><td className="p-3">{row.invoice_number ?? '—'}</td></tr>)}
    </tbody></table>{!count && !busy && <p className="p-5 text-sm text-slate-500">{text('empty')}</p>}</div>
    <div className="flex justify-end gap-2"><button className={button} disabled={busy || !offset} onClick={() => setOffset(Math.max(0, offset - 100))}>{text('previous')}</button><button className={button} disabled={busy || count < 100} onClick={() => setOffset(offset + 100)}>{text('next')}</button></div>
  </section>;
}
