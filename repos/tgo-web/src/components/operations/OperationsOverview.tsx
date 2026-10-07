import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight, Building2, Clock3, ShieldCheck } from 'lucide-react';
import { operationsApi } from '../../services/operationsApi';
import { OpsFeedback, OpsPanel, opsButton, useOpsResource } from './ui';

export default function OperationsOverview() {
  const { t, i18n } = useTranslation();
  const resource = useOpsResource(operationsApi.overview);
  const metrics = [
    ['total_companies', 'totalCompanies', '/ops/companies', Building2],
    ['enabled_companies', 'enabledCompanies', '/ops/companies?state=enabled', ShieldCheck],
    ['expiring_companies', 'expiringCompanies', '/ops/companies?expiring=true', Clock3],
    ['unresolved_orders', 'unresolvedOrders', '/ops/finance?tab=orders', ArrowUpRight],
    ['failed_tasks', 'failedTasks', '/ops/monitor?tab=tasks', ArrowUpRight],
    ['pending_invoices', 'pendingInvoices', '/ops/finance?tab=invoices', ArrowUpRight],
  ] as const;
  return <div className="space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-slate-500">{resource.data ? t('opsWorkspace.checkedAt', { time: new Date(resource.data.checked_at).toLocaleString(i18n.language) }) : ''}</p><button className={opsButton} disabled={resource.busy} onClick={resource.reload}>{t('opsWorkspace.refresh')}</button></div>
    <OpsFeedback {...resource} />
    {resource.data && <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{metrics.map(([field, label, href, Icon]) => <Link key={field} to={href} className="group rounded-xl border border-slate-200 bg-white p-6 transition-colors hover:border-indigo-300 focus-visible:outline-2 focus-visible:outline-indigo-500">
      <div className="flex items-center justify-between"><span className="text-sm text-slate-500">{t(`opsWorkspace.${label}`)}</span><Icon className="h-5 w-5 text-indigo-500" aria-hidden="true" /></div><p className="mt-4 text-3xl font-semibold tracking-tight tabular-nums">{resource.data?.[field]}</p>
    </Link>)}</div>}
    <OpsPanel><h2 className="font-semibold">{t('opsWorkspace.quickActions')}</h2><div className="mt-4 flex flex-wrap gap-3"><Link to="/ops/companies" className={opsButton}>{t('opsWorkspace.manageCompanies')}</Link><Link to="/ops/models" className={opsButton}>{t('opsWorkspace.configureModels')}</Link><Link to="/ops/plans" className={opsButton}>{t('opsWorkspace.managePlans')}</Link></div><p className="mt-5 text-xs leading-6 text-slate-500">{t('opsWorkspace.overviewNote')}</p></OpsPanel>
  </div>;
}
