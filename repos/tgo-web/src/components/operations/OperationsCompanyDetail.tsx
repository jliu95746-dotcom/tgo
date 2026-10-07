import { useCallback, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from 'lucide-react';
import { operationsApi } from '../../services/operationsApi';
import OperationsAuthorization from './OperationsAuthorization';
import OperationsCompanyActions, { type CompanyAction } from './OperationsCompanyActions';
import { AuditEntries } from './OperationsAuditLog';
import { OpsBadge, OpsFeedback, OpsPanel, opsButton, useOpsResource } from './ui';

const tabs = ['basic', 'members', 'authorization', 'history'] as const;

export default function OperationsCompanyDetail() {
  const { t, i18n } = useTranslation();
  const { companyId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = tabs.find(item => item === params.get('tab')) || 'basic';
  const load = useCallback(() => operationsApi.companyDetail(companyId), [companyId]);
  const resource = useOpsResource(load);
  const [action, setAction] = useState<CompanyAction | null>(null);
  const [notice, setNotice] = useState('');
  const saved = (value = t('opsWorkspace.saved')) => { setAction(null); setNotice(value); resource.reload(); };
  const detail = resource.data;
  const date = (value: string | null) => value ? new Date(value).toLocaleString(i18n.language) : '—';
  return <div className="space-y-5"><Link to="/ops/companies" className="inline-flex items-center gap-2 text-sm text-indigo-600"><ArrowLeft className="h-4 w-4" aria-hidden="true" />{t('opsWorkspace.back')}</Link>
    {notice && <p role="status" className="rounded-lg bg-emerald-50 p-4 text-sm text-emerald-800">{notice}</p>}<OpsFeedback {...resource} />
    {detail && <>
      <OpsPanel><div className="flex flex-wrap items-center justify-between gap-4"><div><div className="flex flex-wrap items-center gap-3"><h2 className="text-xl font-semibold">{detail.company.name}</h2><OpsBadge state={detail.company.status} /></div><p className="mt-2 break-all text-xs text-slate-500">{detail.company.id}</p></div><div className="flex flex-wrap gap-2"><button className={opsButton} onClick={resource.reload}>{t('opsWorkspace.refresh')}</button>{detail.company.status !== 'legacy' && <><button className={opsButton} onClick={() => setAction({ kind: 'credits', company: detail.company })}>{t('opsWorkspace.adjustCredits')}</button><button className={opsButton} onClick={() => setAction({ kind: 'state', company: detail.company })}>{t(detail.company.status === 'suspended' ? 'opsWorkspace.restore' : 'opsWorkspace.suspend')}</button></>}</div></div></OpsPanel>
      <div className="flex flex-wrap gap-2 border-b border-slate-200 pb-3" role="group">{tabs.map(item => <button aria-pressed={item === tab} key={item} className={`${opsButton} ${item === tab ? '!border-indigo-200 !bg-indigo-50 !text-indigo-700' : ''}`} onClick={() => setParams({ tab: item })}>{t(`opsWorkspace.${item}`)}</button>)}</div>
      {tab === 'basic' && <OpsPanel><dl className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">{[
        ['plan', detail.company.plan_name || t('opsWorkspace.unnamed')], ['expiry', date(detail.company.expires_at)], ['seats', `${detail.company.used + detail.company.reserved} / ${detail.company.seats ?? '—'}`], ['credits', detail.company.ai_remaining.toLocaleString(i18n.language)], ['created', date(detail.created_at)], ['override', date(detail.operator_override_until)],
      ].map(([label, value]) => <div key={label}><dt className="text-xs text-slate-500">{t(`opsWorkspace.${label}`)}</dt><dd className="mt-2 text-sm font-medium">{value}</dd></div>)}</dl></OpsPanel>}
      {tab === 'authorization' && <OperationsAuthorization detail={detail} onSaved={() => saved()} />}
      {tab === 'members' && <OpsPanel><p className="mb-5 text-sm leading-6 text-slate-500">{t('opsWorkspace.memberHint')}</p><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-slate-500"><tr>{['account', 'role', 'state', 'openSessions', 'actions'].map(item => <th key={item} className="px-3 py-3 font-medium">{t(`opsWorkspace.${item}`)}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{detail.members.map(member => <tr key={member.id}><th className="px-3 py-4 text-left font-medium">{member.username}<p className="mt-1 text-xs font-normal text-slate-500">{member.name} · {t(member.email_verified ? 'opsWorkspace.verified' : 'opsWorkspace.unverified')}</p></th><td className="whitespace-nowrap px-3 py-4">{t(member.role === 'admin' ? 'opsWorkspace.admin' : 'opsWorkspace.user')}</td><td className="whitespace-nowrap px-3 py-4">{t(member.account_enabled ? 'opsWorkspace.enabled' : 'opsWorkspace.disabled')}</td><td className="px-3 py-4 tabular-nums">{member.open_sessions}</td><td className="px-3 py-4"><div className="flex flex-wrap gap-2"><button className={opsButton} onClick={() => setAction({ kind: 'member', company: detail.company, member })}>{t('opsWorkspace.editMember')}</button><button className={opsButton} disabled={!member.email_verified || !member.account_enabled} onClick={() => setAction({ kind: 'email', company: detail.company, member })}>{t('opsWorkspace.resetEmail')}</button></div></td></tr>)}{!detail.members.length && <tr><td colSpan={5} className="py-10 text-center text-slate-500">{t('opsWorkspace.empty')}</td></tr>}</tbody></table></div></OpsPanel>}
      {tab === 'history' && <AuditEntries entries={detail.audits} />}
    </>}
    {action && <OperationsCompanyActions action={action} onClose={() => setAction(null)} onSaved={saved} />}
  </div>;
}
