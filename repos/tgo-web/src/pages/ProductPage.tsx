import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { MessageSquare, Users, BookOpen, ArrowRight } from 'lucide-react';
import { billingApi } from '../services/billingApi';
import { companyMembersApi } from '../services/companyMembersApi';
import type { BillingPlan } from '../types/billing';

export default function ProductPage() {
  const { t, i18n } = useTranslation();
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [annual, setAnnual] = useState(false);
  useEffect(() => {
    let disposed = false;
    void companyMembersApi.status().then(status => status.enabled ? billingApi.plans() : [])
      .then(result => { if (!disposed) setPlans(result); })
      .catch(() => { if (!disposed) setError(true); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, []);
  return <main className="min-h-screen bg-[#f7f8fc] text-slate-900">
    <header className="mx-auto flex max-w-6xl items-center justify-between gap-5 px-6 py-6">
      <Link to="/product" className="flex items-center gap-3 font-semibold"><MessageSquare className="text-indigo-600" aria-hidden="true" />{t('product.brand')}</Link>
      <Link to="/login" className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm">{t('product.login')}</Link>
    </header>
    <section className="mx-auto max-w-6xl px-6 pb-24 pt-16 md:pt-24">
      <p className="mb-6 text-sm font-medium text-indigo-600">{t('product.eyebrow')}</p>
      <h1 className="max-w-3xl text-4xl font-semibold leading-tight tracking-tight md:text-6xl">{t('product.title')}</h1>
      <p className="mt-7 max-w-2xl text-lg leading-8 text-slate-600">{t('product.description')}</p>
      <Link to="/register" className="mt-9 inline-flex items-center gap-4 rounded-xl bg-indigo-600 px-6 py-3.5 font-medium text-white hover:bg-indigo-700">{t('product.start')}<ArrowRight size={18} aria-hidden="true" /></Link>
      <p className="mt-4 max-w-xl text-sm leading-6 text-slate-500">{t('product.trial')}</p>
    </section>
    <section className="border-y border-slate-200 bg-white py-16"><div className="mx-auto max-w-6xl px-6">
      <h2 className="text-2xl font-semibold">{t('product.features')}</h2><div className="mt-9 grid gap-8 md:grid-cols-3">
        {([{ name: 'team', Icon: Users }, { name: 'knowledge', Icon: BookOpen }, { name: 'channels', Icon: MessageSquare }]).map(({ name, Icon }) => <article key={name}>
          <Icon className="mb-5 text-indigo-600" size={28} aria-hidden="true" /><h3 className="text-lg font-semibold">{t(`product.${name}`)}</h3><p className="mt-3 text-sm leading-7 text-slate-600">{t(`product.${name}Text`)}</p>
        </article>)}
      </div></div></section>
    <section className="mx-auto max-w-6xl px-6 py-16"><h2 className="text-3xl font-semibold">{t('product.plans')}</h2><p className="mt-4 text-slate-600">{t('product.pricesHint')}</p>
      <div className="mt-7 flex gap-2">{[false, true].map(value => <button key={String(value)} aria-pressed={annual === value} onClick={() => setAnnual(value)} className={`rounded-lg px-5 py-2 text-sm ${annual === value ? 'bg-slate-900 text-white' : 'border border-slate-300 bg-white'}`}>{t(value ? 'billing.year' : 'billing.month')}</button>)}</div>
      {loading && <p role="status" className="mt-8">{t('product.loading')}</p>}{error && <p role="alert" className="mt-8 text-red-700">{t('product.error')}</p>}
      {!loading && !error && plans.length === 0 && <p className="mt-8 rounded-xl border border-slate-200 bg-white p-7 text-slate-500">{t('product.noPlans')}</p>}
      <div className="mt-8 grid gap-5 md:grid-cols-3">{plans.map(plan => <article key={plan.id} className="rounded-2xl border border-slate-200 bg-white p-7">
        <h3 className="text-xl font-semibold">{plan.definition.name}</h3><p className="my-6 text-4xl font-semibold">{new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'CNY', maximumFractionDigits: 2 }).format((annual ? plan.definition.annual_price : plan.definition.monthly_price) / 100)}</p>
        <ul className="space-y-3 text-sm text-slate-600"><li>{t('billing.planSeats', { count: plan.definition.seats })}</li><li>{t('billing.planAi', { count: plan.definition.monthly_ai })}</li><li>{t('billing.planChannels', { count: plan.definition.channel_limit })}</li></ul>
        <Link to="/settings/billing" className="mt-7 inline-block text-sm font-medium text-indigo-600 hover:underline">{t('product.more')}</Link>
      </article>)}</div>
    </section><footer className="border-t border-slate-200 px-6 py-7 text-center text-sm text-slate-500">{t('product.footer')}</footer>
  </main>;
}
