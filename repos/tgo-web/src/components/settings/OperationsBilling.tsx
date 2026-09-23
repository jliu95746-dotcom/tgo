import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { BillingPlan, PlanDefinition } from '../../types/billing';
import OperationsSupport from './OperationsSupport';
import OperationsCompanies from './OperationsCompanies';
import OperationsTasks from './OperationsTasks';
import OperationsHealth from './OperationsHealth';
import CommercialReadiness from './CommercialReadiness';
import OperationsModelUsage from './OperationsModelUsage';
import OperationsTrialPolicy from './OperationsTrialPolicy';
import OperationsTrialCodes from './OperationsTrialCodes';
import OperationsModels from './OperationsModels';

const numericFields = ['rank', 'monthly_price', 'annual_price', 'seats', 'monthly_ai', 'knowledge_bytes', 'channel_limit', 'seat_monthly_price', 'seat_annual_price', 'ai_pack_price', 'ai_pack_replies'] as const;
const priceFields = new Set<string>(['monthly_price', 'annual_price', 'seat_monthly_price', 'seat_annual_price', 'ai_pack_price']);
const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-40 hover:bg-slate-50';

export default function OperationsBilling() {
  const { t, i18n } = useTranslation();
  const [opened, setOpened] = useState(false);
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<BillingPlan | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const load = async () => { setPlans(await operationsApi.plans()); };
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try { await action(); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t('operationsBilling.error')); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (selected) dialog.current?.showModal(); else dialog.current?.close();
  }, [selected]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const values: Partial<PlanDefinition> = { name: form.name?.trim() };
    for (const field of numericFields) {
      const input = form[field] ?? '';
      if (!(priceFields.has(field) ? /^\d+(\.\d{1,2})?$/.test(input) : /^\d+$/.test(input))) {
        setError(t('operationsBilling.invalid')); return;
      }
      values[field] = priceFields.has(field) ? Math.round(Number(input) * 100) : Number(input);
    }
    if (!values.name || !/^[a-z][a-z0-9_-]{1,39}$/.test(form.code ?? '')) { setError(t('operationsBilling.invalid')); return; }
    void act(async () => { await operationsApi.createPlan(form.code, values as PlanDefinition); setForm({}); });
  };
  return <section className="mt-10 border-t border-slate-200 pt-8">
    <h2 className="text-2xl font-semibold">{t('operationsBilling.title')}</h2>
    <p className="my-3 text-sm leading-6 text-slate-500">{t('operationsBilling.hint')}</p>
    {!opened && <button className={button} disabled={busy} onClick={() => { setOpened(true); void act(async () => undefined); }}>{t('operationsBilling.open')}</button>}
    {error && <p role="alert" className="my-4 rounded-lg bg-red-50 p-4 text-sm text-red-800">{error}</p>}
    <CommercialReadiness />
    <OperationsSupport />
    <OperationsCompanies />
    <OperationsHealth />
    <OperationsTasks />
    <OperationsModelUsage />
    <OperationsTrialPolicy />
    <OperationsTrialCodes />
    <OperationsModels />
    {opened && <><button className={`${button} mb-4`} disabled={busy} onClick={() => void act(async () => undefined)}>{t('billing.refresh')}</button>
      <form onSubmit={submit} className="grid gap-4 rounded-xl border border-slate-200 bg-white p-5 sm:grid-cols-2 lg:grid-cols-3">
        {(['code', 'name', ...numericFields] as const).map(field => <label key={field} className="text-sm"><span className="mb-2 block">{t(`operationsBilling.${field}`)}</span>
          <input required value={form[field] ?? ''} onChange={event => setForm({ ...form, [field]: event.target.value })} maxLength={field === 'name' ? 100 : 40} inputMode={numericFields.includes(field as typeof numericFields[number]) ? 'decimal' : 'text'} className="w-full rounded-lg border border-slate-300 px-3 py-2" /></label>)}
        <div className="sm:col-span-2 lg:col-span-3"><button className={button} type="submit" disabled={busy}>{t('operationsBilling.save')}</button></div>
      </form>
      <div className="mt-5 grid gap-4 md:grid-cols-3">{plans.map(plan => <article key={plan.id} className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="font-semibold">{plan.definition.name}</h3><p className="my-2 text-xs text-slate-500">{plan.code} · {t('operationsBilling.version', { version: plan.version })} · {t(`operationsBilling.${plan.status}`)}</p>
        <p className="mb-4">{new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'CNY' }).format(plan.definition.monthly_price / 100)} / {t('billing.month')}</p>
        {plan.status === 'draft' && <button className={button} disabled={busy} onClick={() => setSelected(plan)}>{t('operationsBilling.publish')}</button>}
        {plan.status === 'published' && <button className={button} disabled={busy} onClick={() => void act(() => operationsApi.changePlanState(plan.id, 'retired'))}>{t('operationsBilling.retire')}</button>}
      </article>)}</div>{plans.length === 0 && <p className="mt-4">{t('operationsBilling.empty')}</p>}
    </>}
    <dialog ref={dialog} onCancel={() => setSelected(null)} aria-labelledby="publish-plan-title" className="m-auto w-[min(92vw,30rem)] rounded-2xl bg-white p-7 backdrop:bg-black/40">
      <h2 id="publish-plan-title" className="text-lg font-semibold">{t('operationsBilling.confirm')}</h2><p className="mt-3">{selected?.definition.name}</p>
      <p className="my-5 text-sm leading-6 text-slate-500">{t('operationsBilling.confirmHint')}</p>
      {error && <p role="alert" className="mb-3 text-sm text-red-700">{error}</p>}
      <div className="flex gap-3"><button className={button} disabled={busy} onClick={() => selected && void act(async () => { await operationsApi.changePlanState(selected.id, 'published'); setSelected(null); })}>{t('operationsBilling.publish')}</button>
      <button className={button} onClick={() => setSelected(null)}>{t('operationsBilling.cancel')}</button></div>
    </dialog>
  </section>;
}
