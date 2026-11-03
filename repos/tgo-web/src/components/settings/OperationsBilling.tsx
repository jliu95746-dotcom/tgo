import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { BillingPlan, PlanDefinition } from '../../types/billing';
import type { OperationsCompany } from '../../types/billingSupport';

const tiers = [
  { code: 'basic', rank: 1, price: '188', subaccounts: '1', replies: '3500' },
  { code: 'standard', rank: 2, price: '388', subaccounts: '3', replies: '10000' },
  { code: 'pro', rank: 3, price: '588', subaccounts: '10', replies: '20000' },
] as const;
type Tier = typeof tiers[number]['code'];
const planFields = ['monthly_price', 'subaccounts', 'monthly_ai'] as const;
const addOnFields = ['seat_monthly_price', 'seat_annual_price', 'ai_pack_price', 'ai_pack_replies'] as const;
type PlanField = typeof planFields[number] | 'annual_price' | 'knowledge_bytes';
type AddOnField = typeof addOnFields[number];
type Draft = Record<PlanField, string>;
type AddOns = Record<AddOnField, string>;
type Confirmation = { kind: 'standard' | 'custom' | 'retire'; plans: BillingPlan[] };
const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-40 hover:bg-slate-50';
const primary = 'rounded-lg bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-40';

function annualPrice(monthlyCents: number) {
  const yearly = Math.floor(monthlyCents * 96 / 1000);
  return (Math.floor((yearly - 8) / 10) * 10 + 8) * 100;
}

function positiveInteger(value: string, allowZero = false) {
  const result = /^\d+$/.test(value) ? Number(value) : NaN;
  if (!Number.isSafeInteger(result) || result < (allowZero ? 0 : 1)) throw new Error();
  return result;
}

function cents(value: string) {
  if (!/^\d+(\.\d{1,2})?$/.test(value)) throw new Error();
  const result = Math.round(Number(value) * 100);
  if (!Number.isSafeInteger(result) || result <= 0) throw new Error();
  return result;
}

function latest(rows: BillingPlan[], code?: string) {
  return rows.filter(row => row.status !== 'retired' && (!code || row.code === code))
    .sort((a, b) => b.version - a.version)[0];
}

function draftOf(plan?: BillingPlan, tier?: typeof tiers[number]): Draft {
  const values = plan?.definition;
  return {
    monthly_price: values ? String(values.monthly_price / 100) : tier?.price ?? '',
    annual_price: values ? String(values.annual_price / 100) : '',
    subaccounts: values ? String(values.seats - 1) : tier?.subaccounts ?? '1',
    monthly_ai: values ? String(values.monthly_ai) : tier?.replies ?? '1000',
    knowledge_bytes: values ? (values.knowledge_bytes / 1e9).toFixed(9).replace(/\.?0+$/, '') : '1',
  };
}

function addOnsOf(plan?: BillingPlan): AddOns {
  const values = plan?.definition;
  return {
    seat_monthly_price: values ? String(values.seat_monthly_price / 100) : '60',
    seat_annual_price: values ? String(values.seat_annual_price / 100) : '568',
    ai_pack_price: values ? String(values.ai_pack_price / 100) : '30',
    ai_pack_replies: values ? String(values.ai_pack_replies) : '1000',
  };
}

function standardForms(rows: BillingPlan[]): Record<Tier, Draft> {
  return {
    basic: draftOf(latest(rows, 'basic'), tiers[0]),
    standard: draftOf(latest(rows, 'standard'), tiers[1]),
    pro: draftOf(latest(rows, 'pro'), tiers[2]),
  };
}

function definitionOf(form: Draft, addOns: AddOns, name: string, rank: number, company?: OperationsCompany): PlanDefinition {
  const monthly = cents(form.monthly_price);
  const annual = company ? cents(form.annual_price) : annualPrice(monthly);
  const capacity = /^\d+(\.\d{1,9})?$/.test(form.knowledge_bytes) ? Math.round(Number(form.knowledge_bytes) * 1e9) : NaN;
  if (annual <= 0 || !Number.isSafeInteger(capacity) || capacity < 1) throw new Error();
  return {
    name, rank, monthly_price: monthly, annual_price: annual,
    seats: 1 + positiveInteger(form.subaccounts, true), channel_limit: null,
    monthly_ai: positiveInteger(form.monthly_ai, true), knowledge_bytes: capacity,
    seat_monthly_price: cents(addOns.seat_monthly_price), seat_annual_price: cents(addOns.seat_annual_price),
    ai_pack_price: cents(addOns.ai_pack_price), ai_pack_replies: positiveInteger(addOns.ai_pack_replies),
    target_project_id: company?.id,
  };
}

export default function OperationsBilling() {
  const { t, i18n } = useTranslation();
  const label = (key: string) => t(`operationsBilling.${key}`);
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [forms, setForms] = useState(() => standardForms([]));
  const [addOns, setAddOns] = useState(() => addOnsOf());
  const [tier, setTier] = useState<Tier>('basic');
  const [custom, setCustom] = useState(false);
  const [companyQuery, setCompanyQuery] = useState('');
  const [companies, setCompanies] = useState<OperationsCompany[]>([]);
  const [searched, setSearched] = useState(false);
  const [company, setCompany] = useState<OperationsCompany | null>(null);
  const [customPlans, setCustomPlans] = useState<BillingPlan[]>([]);
  const [customForm, setCustomForm] = useState(() => draftOf());
  const [customAddOns, setCustomAddOns] = useState(() => addOnsOf());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const advanced = useRef<HTMLDetailsElement>(null);

  const receivePlans = useCallback((rows: BillingPlan[]) => {
    setPlans(rows);
    setForms(standardForms(rows));
    setAddOns(addOnsOf(latest(rows, 'basic') ?? latest(rows)));
  }, []);

  useEffect(() => {
    let active = true;
    setBusy(true);
    void operationsApi.plans().then(rows => { if (active) receivePlans(rows); })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : t('operationsBilling.error')); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [receivePlans, t]);

  useEffect(() => {
    if (confirmation) dialog.current?.showModal();
    else dialog.current?.close();
  }, [confirmation]);

  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try { await operation(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : label('error')); }
    finally { setBusy(false); }
  };

  const refreshStandard = async () => { receivePlans(await operationsApi.plans()); };
  const openCompany = (value: OperationsCompany) => void run(async () => {
    const rows = await operationsApi.plans(value.id);
    const previous = latest(rows);
    setCompany(value);
    setCustomPlans(rows);
    setCustomForm(draftOf(previous));
    setCustomAddOns(previous ? addOnsOf(previous) : { ...addOns });
  });

  const save = (event: FormEvent) => {
    event.preventDefault();
    let payload: { code: string; definition: PlanDefinition }[];
    try {
      if (custom && company) {
        const name = `${company.name.slice(0, 84)} · ${label('custom')}`;
        payload = [{
          code: `custom-${company.id.replace(/-/g, '')}`,
          definition: definitionOf(customForm, customAddOns, name, 4, company),
        }];
      } else {
        payload = tiers.map(item => ({
          code: item.code,
          definition: definitionOf(forms[item.code], addOns, label(item.code), item.rank),
        }));
      }
    } catch {
      if (advanced.current) advanced.current.open = true;
      setError(label('invalid'));
      return;
    }
    void run(async () => {
      if (custom && company) {
        await operationsApi.createPlan(payload[0]!.code, payload[0]!.definition);
        setCustomPlans(await operationsApi.plans(company.id));
      } else {
        await operationsApi.saveStandardDrafts(payload);
        await refreshStandard();
      }
      setNotice(label('saved'));
    });
  };

  const applyConfirmation = () => {
    if (!confirmation) return;
    void run(async () => {
      if (confirmation.kind === 'standard') {
        await operationsApi.publishStandardDrafts(confirmation.plans.map(plan => plan.id));
        await refreshStandard();
      } else if (company) {
        await operationsApi.changePlanState(confirmation.plans[0]!.id, confirmation.kind === 'retire' ? 'retired' : 'published');
        setCustomPlans(await operationsApi.plans(company.id));
      }
      setConfirmation(null);
      setNotice(label('changed'));
    });
  };

  const money = new Intl.NumberFormat(i18n.language, { style: 'currency', currency: 'CNY', maximumFractionDigits: 2, minimumFractionDigits: 0 });
  const draftBatch = tiers.map(item => latest(plans, item.code)).filter((row): row is BillingPlan => row?.status === 'draft');
  const customPlan = latest(customPlans);
  const form = custom ? customForm : forms[tier];
  const policy = custom ? customAddOns : addOns;
  const updatePlan = (field: PlanField, value: string) => custom
    ? setCustomForm(previous => ({ ...previous, [field]: value }))
    : setForms(previous => ({ ...previous, [tier]: { ...previous[tier], [field]: value } }));
  const updateAddOn = (field: AddOnField, value: string) => custom
    ? setCustomAddOns(previous => ({ ...previous, [field]: value }))
    : setAddOns(previous => ({ ...previous, [field]: value }));
  const input = (field: PlanField | AddOnField, value: string, update: (value: string) => void) => (
    <label key={field} className="block min-w-0 text-sm">
      <span className="mb-2 block">{label(field)}</span>
      <input value={value} inputMode="decimal" onChange={event => update(event.target.value)}
        className="w-full rounded-lg border border-slate-300 px-3 py-2" />
    </label>
  );
  const annual = (value: string) => {
    try { const amount = annualPrice(cents(value)); return amount > 0 ? money.format(amount / 100) : '—'; }
    catch { return '—'; }
  };
  return <section className="rounded-xl border border-slate-200 bg-white p-5 sm:p-6">
    <h2 className="text-2xl font-semibold">{label('title')}</h2>
    <p className="my-3 text-sm text-slate-600">{label('sameService')}</p>
    <div className="my-4 flex flex-wrap gap-3">
      <button className={button} type="button" aria-pressed={!custom} onClick={() => { setCustom(false); setError(''); }}>{label('standardPlans')}</button>
      <button className={button} type="button" aria-pressed={custom} onClick={() => { setCustom(true); setError(''); }}>{label('custom')}</button>
    </div>
    {notice && <p role="status" className="my-3 text-sm text-green-800">{notice}</p>}
    {error && <p role="alert" className="my-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {!custom && <div className="mb-5 grid gap-3 md:grid-cols-3">
      {tiers.map(item => {
        const draft = forms[item.code];
        const row = latest(plans, item.code);
        return <button key={item.code} type="button" aria-pressed={tier === item.code}
          className={`rounded-xl border p-4 text-left ${tier === item.code ? 'border-blue-600 bg-blue-50' : 'border-slate-200'}`}
          onClick={() => { setTier(item.code); setError(''); }}>
          <span className="block font-medium">{label(item.code)}</span>
          <span className="my-1 block text-sm">{t('operationsBilling.cardPrice', { monthly: draft.monthly_price || '—', annual: annual(draft.monthly_price) })}</span>
          <span className="block text-xs text-slate-600">{t('operationsBilling.cardQuota', { subaccounts: draft.subaccounts })}</span>
          <span className="block text-sm font-medium">{t('operationsBilling.cardAi', { replies: draft.monthly_ai ? Number(draft.monthly_ai).toLocaleString(i18n.language) : '—' })}</span>
          <span className="block text-xs text-slate-600">{label('channelsIncluded')}</span>
          {typeof row?.definition.channel_limit === 'number' && <span className="block text-xs text-slate-500">{t('operationsBilling.previousChannelCap', { count: row.definition.channel_limit })}</span>}
          {row && <span className="mt-2 block text-xs text-slate-500">{label(row.status)}</span>}
        </button>;
      })}
    </div>}
    {custom && <div className="mb-4 rounded-xl border border-slate-200 p-4">
      <p className="mb-3 text-sm text-slate-600">{label('customHint')}</p>
      <form className="flex flex-wrap gap-2" onSubmit={event => {
        event.preventDefault();
        void run(async () => {
          const result = await operationsApi.companyDirectory(0, companyQuery.trim());
          setCompanies(result.data);
          setSearched(true);
        });
      }}>
        <input aria-label={label('company')} value={companyQuery} onChange={event => setCompanyQuery(event.target.value)}
          placeholder={label('company')} className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2" />
        <button className={button} disabled={busy}>{label('search')}</button>
      </form>
      {searched && <div className="mt-3 flex flex-wrap gap-2">
        {companies.length === 0 && <p className="text-sm text-slate-500">{label('noCompany')}</p>}
        {companies.map(item => <button type="button" key={item.id} className={button}
          disabled={busy} aria-pressed={company?.id === item.id} onClick={() => openCompany(item)}>{item.name}</button>)}
      </div>}
      {company && <p className="mt-3 text-sm font-medium">{t('operationsBilling.forCompany', { name: company.name })}</p>}
    </div>}
    {(!custom || company) && <form className="rounded-xl border border-slate-200 p-4" onSubmit={save}>
      <h3 className="mb-3 font-semibold">{custom ? label('custom') : label(tier)}</h3>
      <div className="grid gap-4 sm:grid-cols-2">
        {planFields.map(field => input(field, form[field], value => updatePlan(field, value)))}
        {custom ? input('annual_price', form.annual_price, value => updatePlan('annual_price', value)) :
          <p className="text-sm text-slate-600">{label('annual_price')} <strong>{annual(form.monthly_price)}</strong><br />{label('annualRule')}</p>}
      </div>
      <details ref={advanced} className="my-4 border-t border-slate-100 pt-3">
        <summary className="cursor-pointer text-sm font-medium">{custom ? label('advanced') : label('commonAddOns')}</summary>
        <p className="mt-2 text-xs text-slate-600">{custom ? label('customAddonHint') : label('sharedAddonHint')}</p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          {input('knowledge_bytes', form.knowledge_bytes, value => updatePlan('knowledge_bytes', value))}
          {addOnFields.map(field => input(field, policy[field], value => updateAddOn(field, value)))}
        </div>
      </details>
      <div className="flex flex-wrap gap-3">
        <button className={primary} type="submit" disabled={busy}>{label(custom ? 'save' : 'saveThree')}</button>
        {!custom && draftBatch.length === tiers.length && <button className={button} type="button" disabled={busy}
          onClick={() => setConfirmation({ kind: 'standard', plans: draftBatch })}>{label('publishThree')}</button>}
        {custom && customPlan?.status === 'draft' && <button className={button} type="button" disabled={busy}
          onClick={() => setConfirmation({ kind: 'custom', plans: [customPlan] })}>{label('publish')}</button>}
        {custom && customPlan?.status === 'published' && <button className={button} type="button" disabled={busy}
          onClick={() => setConfirmation({ kind: 'retire', plans: [customPlan] })}>{label('retire')}</button>}
      </div>
    </form>}
    <dialog ref={dialog} onCancel={() => setConfirmation(null)} aria-labelledby="publish-plan-title"
      className="m-auto w-[min(92vw,32rem)] rounded-2xl bg-white p-6 backdrop:bg-black/40">
      <h3 id="publish-plan-title" className="font-semibold">{label(confirmation?.kind === 'retire' ? 'retire' : 'confirm')}</h3>
      <p className="my-3 text-sm text-slate-600">{label(confirmation?.kind === 'standard' ? 'confirmHint' : 'customConfirmHint')}</p>
      <ul className="my-3 space-y-2 text-sm">{confirmation?.plans.map(plan => <li key={plan.id}>
        {plan.definition.name} · {money.format(plan.definition.monthly_price / 100)}/{t('billing.month')} · {money.format(plan.definition.annual_price / 100)}/{t('billing.year')}
      </li>)}</ul>
      {error && <p role="alert" className="my-3 text-sm text-red-800">{error}</p>}
      <div className="flex gap-3">
        <button type="button" className={primary} disabled={busy} onClick={applyConfirmation}>{label(confirmation?.kind === 'retire' ? 'retire' : 'publish')}</button>
        <button type="button" className={button} disabled={busy} onClick={() => setConfirmation(null)}>{label('cancel')}</button>
      </div>
    </dialog>
  </section>;
}
