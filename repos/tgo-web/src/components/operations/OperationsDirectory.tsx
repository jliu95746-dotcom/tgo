import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Search } from 'lucide-react';
import { operationsApi } from '../../services/operationsApi';
import { OpsBadge, OpsFeedback, opsButton, opsInput, useOpsResource } from './ui';

const states = ['enabled', 'legacy', 'pending', 'trial', 'active', 'expired', 'suspended'];

export default function OperationsDirectory() {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();
  const query = params.get('q') || '';
  const state = states.includes(params.get('state') || '') ? params.get('state') || '' : '';
  const expiring = params.get('expiring') === 'true';
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  const [search, setSearch] = useState(query);
  useEffect(() => setSearch(query), [query]);
  const load = useCallback(() => operationsApi.companyDirectory(offset, query, state, expiring), [offset, query, state, expiring]);
  const resource = useOpsResource(load);
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(params); next.set('offset', '0');
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  };
  const submit = (event: FormEvent) => { event.preventDefault(); change('q', search.trim()); };
  const page = (nextOffset: number) => { const next = new URLSearchParams(params); next.set('offset', String(nextOffset)); setParams(next); };
  return <div className="space-y-5">
    <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-5">
      <form onSubmit={submit} className="flex min-w-60 flex-1 items-end gap-2"><label className="flex-1 text-sm">{t('opsWorkspace.searchLabel')}<input maxLength={254} value={search} onChange={event => setSearch(event.target.value)} className={opsInput} /></label><button type="submit" className={opsButton}><Search className="h-4 w-4" aria-hidden="true" />{t('opsWorkspace.search')}</button></form>
      <label className="min-w-36 text-sm">{t('opsWorkspace.state')}<select className={opsInput} value={state} onChange={event => change('state', event.target.value)}><option value="">{t('opsWorkspace.allStates')}</option>{states.map(item => <option value={item} key={item}>{t(`opsWorkspace.states.${item}`)}</option>)}</select></label>
      <label className="flex items-center gap-2 py-2.5 text-sm"><input type="checkbox" checked={expiring} onChange={event => change('expiring', event.target.checked ? 'true' : '')} />{t('opsWorkspace.expiring')}</label><button className={opsButton} disabled={resource.busy} onClick={resource.reload}>{t('opsWorkspace.refresh')}</button>
    </div>
    <OpsFeedback {...resource} />
    {resource.data && <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <p className="border-b border-slate-200 px-5 py-4 text-sm font-medium">{t('opsWorkspace.total', { count: resource.data.total })}</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">{t('opsWorkspace.companies')}</caption><thead className="bg-slate-50 text-xs text-slate-500"><tr>{['company', 'plan', 'state', 'expiry', 'seats', 'credits', 'actions'].map(key => <th key={key} scope="col" className="whitespace-nowrap px-5 py-3 font-medium">{t(`opsWorkspace.${key}`)}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{resource.data.data.map(company => <tr key={company.id} className="hover:bg-slate-50/70">
        <th scope="row" className="min-w-40 px-5 py-5 font-medium"><Link to={`/ops/companies/${company.id}`} className="hover:text-indigo-600">{company.name}</Link>{company.order_exceptions > 0 && <p className="mt-1 text-xs font-normal text-amber-700">{t('opsWorkspace.exceptions')} · {company.order_exceptions}</p>}</th><td className="whitespace-nowrap px-5 py-5">{company.plan_name || t('opsWorkspace.unnamed')}</td><td className="px-5 py-5"><OpsBadge state={company.status} /></td><td className="whitespace-nowrap px-5 py-5 text-slate-500">{company.expires_at ? new Date(company.expires_at).toLocaleDateString(i18n.language) : '—'}</td><td className="px-5 py-5 tabular-nums">{company.used + company.reserved} / {company.seats ?? '—'}{company.reserved > 0 && <p className="mt-1 whitespace-nowrap text-xs text-slate-500">{t('opsWorkspace.reserved', { count: company.reserved })}</p>}</td><td className="px-5 py-5 tabular-nums">{company.ai_remaining.toLocaleString(i18n.language)}</td><td className="px-5 py-5"><Link to={`/ops/companies/${company.id}`} className="whitespace-nowrap font-medium text-indigo-600 hover:underline">{t('opsWorkspace.detail')}</Link></td>
      </tr>)}{!resource.data.data.length && <tr><td colSpan={7} className="px-5 py-12 text-center text-slate-500">{t('opsWorkspace.empty')}</td></tr>}</tbody></table></div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-4"><span className="text-sm text-slate-500">{t('opsWorkspace.page', { page: Math.floor(offset / 20) + 1 })}</span><div className="flex gap-2"><button className={opsButton} disabled={resource.busy || offset === 0} onClick={() => page(Math.max(0, offset - 20))}>{t('opsWorkspace.previous')}</button><button className={opsButton} disabled={resource.busy || offset + 20 >= resource.data.total} onClick={() => page(offset + 20)}>{t('opsWorkspace.next')}</button></div></div>
    </div>}
  </div>;
}
