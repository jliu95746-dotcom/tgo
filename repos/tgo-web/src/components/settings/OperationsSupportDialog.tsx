import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { BillingOrder } from '../../types/billing';
import type { InvoiceRequest, QuotaReply, Refund } from '../../types/billingSupport';

export type SupportSelection = { kind: 'refund'; order: BillingOrder } | { kind: 'preview'; refund: Refund }
  | { kind: 'invoice'; invoice: InvoiceRequest } | { kind: 'quota'; reply: QuotaReply };
const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-40';
const input = 'mt-2 w-full rounded-lg border border-slate-300 px-3 py-2';

export default function OperationsSupportDialog({ selection, onClose, onComplete }: {
  selection: SupportSelection; onClose: () => void; onComplete: () => void;
}) {
  const { t, i18n } = useTranslation();
  const text = (key: string) => t(`billingSupport.${key}`);
  const [selected, setSelected] = useState(selection);
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [action, setAction] = useState<'keep' | 'suspend'>('keep');
  const [result, setResult] = useState<'issued' | 'rejected'>('issued');
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [quotaAction, setQuotaAction] = useState<'settle' | 'release'>('settle');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const money = (value: number) => new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'CNY' }).format(value / 100);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError('');
    try {
      if (selected.kind === 'refund') {
        if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0 || reason.trim().length < 5) throw new Error(text('invalid'));
        const refund = await operationsApi.previewRefund({ order_id: selected.order.id, amount: Math.round(Number(amount) * 100), reason: reason.trim(), entitlement_action: action });
        setSelected({ kind: 'preview', refund });
      } else if (selected.kind === 'preview') {
        await operationsApi.confirmRefund(selected.refund.id); onComplete();
      } else if (selected.kind === 'invoice') {
        await operationsApi.processInvoice(selected.invoice.id, { status: result, invoice_number: result === 'issued' ? invoiceNumber.trim() : undefined, reason: reason.trim() }); onComplete();
      } else {
        await operationsApi.resolveQuota(selected.reply.id, { action: quotaAction, reason: reason.trim() }); onComplete();
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : text('error')); }
    finally { setBusy(false); }
  };
  return <dialog ref={dialog} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }} aria-labelledby="ops-support-title" className="m-auto max-h-[90vh] w-[min(92vw,36rem)] overflow-y-auto rounded-2xl bg-white p-7 backdrop:bg-black/40">
    <h2 id="ops-support-title" className="text-xl font-semibold">{text(selected.kind === 'invoice' ? 'invoiceProcess' : selected.kind === 'quota' ? 'quotaReview' : 'previewTitle')}</h2>
    <form className="mt-5 space-y-4" onSubmit={event => void submit(event)}>
      {(selected.kind === 'refund' || selected.kind === 'preview') && <p className="text-sm leading-6 text-slate-500">{text('refundHint')}</p>}
      {selected.kind === 'refund' && <><p className="break-all font-mono text-xs">{selected.order.number}</p><p>{text('original')}: {money(selected.order.amount)}</p><p>{text('refunded')}: {money(selected.order.refunded_amount)}</p>
        <label className="block text-sm">{text('refundAmount')}<input required className={input} inputMode="decimal" value={amount} onChange={event => setAmount(event.target.value)} /></label>
        <label className="block text-sm">{text('action')}<select className={input} value={action} onChange={event => setAction(event.target.value as typeof action)}><option value="keep">{text('keep')}</option><option value="suspend">{text('suspend')}</option></select></label></>}
      {selected.kind === 'preview' && <dl className="space-y-3 text-sm"><div><dt>{text('amount')}</dt><dd className="text-2xl font-semibold">{money(selected.refund.amount)}</dd></div><div><dt>{text('original')}</dt><dd>{money(selected.refund.disposition.original_order_amount)}</dd></div><div><dt>{text('refunded')}</dt><dd>{money(selected.refund.disposition.already_refunded)}</dd></div><div><dt>{text('orderAi')}</dt><dd>{selected.refund.disposition.granted_ai_from_order} / {selected.refund.disposition.available_ai_from_order}</dd></div><div><dt>{text('action')}</dt><dd>{text(selected.refund.disposition.action)}</dd></div><div><dt>{text('reason')}</dt><dd>{selected.refund.reason}</dd></div></dl>}
      {selected.kind === 'invoice' && <><p>{selected.invoice.title}</p><label className="block text-sm">{text('invoiceResult')}<select className={input} value={result} onChange={event => setResult(event.target.value as typeof result)}><option value="issued">{text('issued')}</option><option value="rejected">{text('rejected')}</option></select></label>{result === 'issued' && <label className="block text-sm">{text('invoiceNumber')}<input required maxLength={100} className={input} value={invoiceNumber} onChange={event => setInvoiceNumber(event.target.value)} /></label>}</>}
      {selected.kind === 'quota' && <><p className="text-sm leading-6 text-slate-500">{text('reviewHint')}</p><label className="block text-sm">{text('action')}<select className={input} value={quotaAction} onChange={event => setQuotaAction(event.target.value as typeof quotaAction)}><option value="settle">{text('settle')}</option><option value="release">{text('release')}</option></select></label></>}
      {selected.kind !== 'preview' && <label className="block text-sm">{text('reason')}<textarea required minLength={5} maxLength={500} className={input} value={reason} onChange={event => setReason(event.target.value)} /></label>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex flex-wrap gap-2"><button type="submit" className={button} disabled={busy}>{text(selected.kind === 'preview' ? 'refundConfirm' : selected.kind === 'refund' ? 'refundPreview' : 'submit')}</button><button type="button" className={button} disabled={busy} onClick={onClose}>{text('cancel')}</button></div>
    </form>
  </dialog>;
}
