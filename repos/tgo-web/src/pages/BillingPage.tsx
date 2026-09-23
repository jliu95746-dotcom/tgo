import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { QRCodeSVG } from 'qrcode.react';
import { useBillingStore } from '../stores/billingStore';
import { useAuthStore } from '../stores/authStore';
import type { BillingOrder, QuoteRequest } from '../types/billing';
import BillingSupport from '../components/settings/BillingSupport';

const button = 'rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium disabled:opacity-40 hover:bg-slate-50';
const primary = 'rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 hover:bg-indigo-700';

export default function BillingPage() {
  const { t, i18n } = useTranslation();
  const user = useAuthStore(state => state.user);
  const admin = user?.role === 'admin';
  const store = useBillingStore();
  const [months, setMonths] = useState<1 | 12>(1);
  const [quantity, setQuantity] = useState(1);
  const [offset, setOffset] = useState(0);
  const [localNow, setNow] = useState(Date.now());
  const now = localNow + store.serverClockOffset;
  const dialog = useRef<HTMLDialogElement>(null);
  const { subscription, plans, quote, order, busy, error } = store;
  const orderId = order?.id;
  const fulfillmentStatus = order?.fulfillment_status;
  const money = (fen: number) => new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'CNY' }).format(fen / 100);
  const date = (value: string | null) => value ? new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : t('billing.none');
  const status = (item: BillingOrder) => item.fulfillment_status === 'applied' ? 'applied'
    : item.fulfillment_status === 'conflict' ? 'conflict' : item.payment_status === 'paid' ? 'paidPending'
    : item.payment_status === 'closed' ? 'closed' : 'paymentPending';
  const requestQuote = (request: QuoteRequest) => void store.getQuote(request);

  useEffect(() => { void useBillingStore.getState().load(admin, offset); }, [admin, offset, user?.id]);
  useEffect(() => () => useBillingStore.getState().reset(), [user?.id]);
  useEffect(() => {
    if (subscription?.months) setMonths(subscription.months);
  }, [subscription?.months]);
  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(clock);
  }, []);
  useEffect(() => {
    if (!orderId || (fulfillmentStatus && ['applied', 'conflict'].includes(fulfillmentStatus))) return;
    const poll = window.setInterval(() => { void useBillingStore.getState().pollOrder(); }, 3000);
    return () => window.clearInterval(poll);
  }, [orderId, fulfillmentStatus]);
  useEffect(() => {
    if ((quote || order) && !dialog.current?.open) dialog.current?.showModal();
    if (!quote && !order) dialog.current?.close();
  }, [quote, order]);

  return <main className="mx-auto max-w-6xl space-y-7 p-5 md:p-8 text-slate-900 dark:text-slate-100">
    <header className="flex flex-wrap items-start justify-between gap-4"><div>
      <h1 className="text-2xl font-semibold">{t('billing.title')}</h1>
      <p className="mt-2 text-sm text-slate-500">{t('billing.subtitle')}</p>
    </div><button className={button} disabled={busy} onClick={() => void store.load(admin, offset)}>{t('billing.refresh')}</button></header>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-4 text-red-800">{error.startsWith('billing.') ? t(error) : error}</p>}
    {busy && !subscription && <p role="status">{t('billing.loading')}</p>}
    {subscription && <section className="rounded-2xl border border-slate-200 bg-white p-6 dark:bg-slate-800">
      <div className="flex flex-wrap justify-between gap-3"><h2 className="font-semibold">{subscription.plan?.definition.name || t('billing.current')}</h2>
        <span className="rounded-full bg-indigo-50 px-3 py-1 text-sm text-indigo-700">{t(`billing.${subscription.status}`)}</span></div>
      <dl className="mt-5 grid gap-5 sm:grid-cols-3">
        <div><dt className="text-sm text-slate-500">{t('billing.expiry')}</dt><dd className="mt-2 font-medium">{date(subscription.expires_at)}</dd></div>
        <div><dt className="text-sm text-slate-500">{t('billing.seats')}</dt><dd className="mt-2 font-medium">{t('billing.seatUsage', { used: subscription.seats_used, limit: subscription.seats ?? '—', reserved: subscription.seats_reserved })}</dd></div>
        <div><dt className="text-sm text-slate-500">{t('billing.ai')}</dt><dd className="mt-2 text-2xl font-semibold">{subscription.ai_remaining.toLocaleString()}</dd></div>
      </dl><p className="mt-5 text-sm leading-6 text-slate-500">{t('billing.quotaHint')}</p><p className="text-sm leading-6 text-slate-500">{t('billing.seatHint')}</p>
    </section>}
    {admin && subscription && <>
      <div className="flex flex-wrap gap-3"><select aria-label={t('billing.period')} className={button} value={months} disabled={Boolean(subscription.months)} onChange={e => setMonths(Number(e.target.value) as 1 | 12)}>
        <option value={1}>{t('billing.month')}</option><option value={12}>{t('billing.year')}</option></select>
        {subscription.plan && <button className={primary} disabled={busy} onClick={() => requestQuote({ kind: 'renew', months })}>{t('billing.renew')}</button>}
      </div>
      <section className="grid gap-4 md:grid-cols-3">{plans.map(plan => <article key={plan.id} className="rounded-2xl border border-slate-200 bg-white p-6 dark:bg-slate-800">
        <h2 className="text-lg font-semibold">{plan.definition.name}</h2><p className="my-5 text-3xl font-semibold">{money(months === 12 ? plan.definition.annual_price : plan.definition.monthly_price)}<span className="ml-2 text-sm font-normal text-slate-500">{t(months === 12 ? 'billing.year' : 'billing.month')}</span></p>
        <ul className="mb-6 space-y-2 text-sm text-slate-600"><li>{t('billing.planSeats', { count: plan.definition.seats })}</li><li>{t('billing.planAi', { count: plan.definition.monthly_ai })}</li><li>{t('billing.planChannels', { count: plan.definition.channel_limit })}</li></ul>
        {!subscription.plan ? <button className={primary} disabled={busy} onClick={() => requestQuote({ kind: 'subscribe', plan_id: plan.id, months })}>{t('billing.subscribe')}</button>
          : plan.definition.rank > subscription.plan.definition.rank && <button className={primary} disabled={busy || subscription.status !== 'active'} onClick={() => requestQuote({ kind: 'upgrade', plan_id: plan.id, months })}>{t('billing.upgrade')}</button>}
      </article>)}{plans.length === 0 && <p>{t('billing.noPlans')}</p>}</section>
      {subscription.plan && <section className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 p-5">
        <label htmlFor="billing-quantity">{t('billing.quantity')}</label><input id="billing-quantity" type="number" min={1} max={10000} value={quantity} onChange={e => setQuantity(Number(e.target.value))} className="w-24 rounded-lg border border-slate-300 p-2" />
        {(['seats', 'ai_pack'] as const).map(kind => <button key={kind} className={button} disabled={busy || subscription.status !== 'active' || !Number.isInteger(quantity) || quantity < 1 || quantity > 10000} onClick={() => requestQuote({ kind, months, quantity })}>{t(kind === 'seats' ? 'billing.seatsAction' : 'billing.aiPackAction')}</button>)}
      </section>}
      <section><h2 className="mb-4 text-lg font-semibold">{t('billing.orders')}</h2><div className="overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full text-left text-sm"><thead><tr>{['orderNumber', 'total', 'current', 'view'].map(key => <th key={key} className="p-3">{t(`billing.${key}`)}</th>)}</tr></thead>
          <tbody>{store.orders.map(item => <tr key={item.id} className="border-t border-slate-200"><td className="p-3 font-mono text-xs">{item.number}</td><td className="p-3">{money(item.amount)}</td><td className="p-3">{t(`billing.${status(item)}`)}</td><td className="p-3"><button className={button} disabled={busy} onClick={() => void store.viewOrder(item.id)}>{t('billing.view')}</button></td></tr>)}</tbody></table>
        {store.orders.length === 0 && <p className="p-5 text-slate-500">{t('billing.noOrders')}</p>}</div>
        <div className="mt-3 flex justify-end gap-2"><button className={button} disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>{t('billing.previous')}</button><button className={button} disabled={busy || store.orders.length < 20} onClick={() => setOffset(offset + 20)}>{t('billing.next')}</button></div>
      </section>
    </>}
    {!admin && <p>{t('billing.adminOnly')}</p>}
    {subscription && <BillingSupport key={user?.id} admin={admin} orders={store.orders} />}
    <dialog ref={dialog} aria-labelledby="billing-dialog-title" onCancel={() => store.close()} className="m-auto w-[min(92vw,32rem)] rounded-2xl bg-white p-7 text-slate-900 shadow-xl backdrop:bg-black/40">
      <h2 id="billing-dialog-title" className="text-xl font-semibold">{t(quote ? 'billing.confirm' : 'billing.cashier')}</h2>
      {error && <p role="alert" className="mt-4 text-sm text-red-700">{error.startsWith('billing.') ? t(error) : error}</p>}
      {quote && <><p className="my-5 text-3xl font-semibold">{money(quote.amount)}</p><dl className="space-y-3 text-sm">
        <div><dt className="text-slate-500">{t('billing.period')}</dt><dd>{date(quote.details.starts_at)} – {date(quote.details.ends_at)}</dd></div>
        <div><dt className="text-slate-500">{t('billing.resultingSeats')}</dt><dd>{quote.details.resulting_seats}</dd></div>
        <div><dt className="text-slate-500">{t('billing.additionalAi')}</dt><dd>{quote.details.additional_ai}</dd></div></dl>
        <p className="my-5 text-sm leading-6 text-slate-500">{t('billing.confirmHint')}</p><button className={primary} disabled={busy || new Date(quote.expires_at).getTime() <= now} onClick={() => void store.pay()}>{t('billing.pay')}</button>
      </>}
      {order && <div className="mt-5 space-y-4 text-center"><p className="break-all font-mono text-xs">{order.number}</p><p className="text-3xl font-semibold">{money(order.amount)}</p>
        <p role="status">{t(`billing.${status(order)}`)}</p>
        {order.payment_status === 'pending' && order.code_url && new Date(order.expires_at).getTime() > now && <><QRCodeSVG value={order.code_url} size={224} marginSize={4} className="mx-auto" /><p className="text-sm text-slate-500">{t('billing.scan')}</p></>}
        {order.payment_status === 'pending' && new Date(order.expires_at).getTime() <= now && <p className="text-sm text-amber-700">{t('billing.qrExpired')}</p>}
        {error && <button className={button} disabled={busy} onClick={() => void store.viewOrder(order.id)}>{t('billing.retry')}</button>}
      </div>}
      <button className={`${button} mt-5`} onClick={() => store.close()}>{t('billing.close')}</button>
    </dialog>
  </main>;
}
