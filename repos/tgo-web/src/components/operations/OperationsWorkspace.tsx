import { lazy, Suspense } from 'react';
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, Building2, CreditCard, LayoutDashboard, ListChecks, LogOut, ShieldCheck, Sparkles, Ticket } from 'lucide-react';
import { useOperationsStore } from '../../stores/operationsStore';
import { opsButton } from './ui';

const Overview = lazy(() => import('./OperationsOverview'));
const Directory = lazy(() => import('./OperationsDirectory'));
const Detail = lazy(() => import('./OperationsCompanyDetail'));
const Models = lazy(() => import('./OperationsModelCenter'));
const Sections = lazy(() => import('./OperationsSections'));
const Audits = lazy(() => import('./OperationsAuditLog'));
const navigation = [
  ['overview', '/ops', LayoutDashboard], ['companies', '/ops/companies', Building2],
  ['models', '/ops/models', Sparkles], ['plans', '/ops/plans', Ticket],
  ['finance', '/ops/finance', CreditCard], ['monitor', '/ops/monitor', Activity], ['audits', '/ops/audits', ListChecks],
] as const;

export default function OperationsWorkspace() {
  const { t } = useTranslation();
  const { operator, loading, logout, error } = useOperationsStore();
  const location = useLocation();
  const current = navigation.find(([, href]) => href !== '/ops' && location.pathname.startsWith(href)) || navigation[0];
  if (location.hash === '#shared-models') return <Navigate to="/ops/models?tab=services" replace />;
  return <div className="min-h-screen bg-slate-50 text-slate-900 lg:flex">
    <aside className="border-b border-slate-200 bg-white lg:sticky lg:top-0 lg:flex lg:h-screen lg:w-60 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r">
      <NavLink to="/ops" className="flex items-center gap-3 px-6 py-7"><ShieldCheck className="h-8 w-8 text-indigo-600" aria-hidden="true" /><div><p className="font-semibold">{t('opsWorkspace.title')}</p><p className="mt-1 text-[11px] text-slate-400">{t('opsWorkspace.subtitle')}</p></div></NavLink>
      <nav aria-label={t('opsWorkspace.title')} className="flex flex-wrap gap-1 px-3 pb-4 lg:flex-col">{navigation.map(([key, href, Icon]) => <NavLink key={key} to={href} end={href === '/ops'} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-indigo-500 ${isActive ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-50'}`}><Icon className="h-[18px] w-[18px]" aria-hidden="true" />{t(`opsWorkspace.${key}`)}</NavLink>)}</nav>
      <div className="hidden border-t border-slate-100 p-5 lg:mt-auto lg:block"><p className="truncate text-sm font-medium">{operator?.name}</p><button className="mt-3 flex items-center gap-2 text-xs text-slate-500 hover:text-indigo-600" disabled={loading} onClick={() => void logout()}><LogOut className="h-4 w-4" aria-hidden="true" />{t('operations.logout')}</button></div>
    </aside>
    <div className="min-w-0 flex-1"><header className="flex min-h-20 items-center justify-between gap-4 border-b border-slate-200 bg-white px-5 sm:px-8"><span className="text-sm text-slate-500">{t('opsWorkspace.title')} <span className="mx-2 text-slate-300">/</span> <span className="font-medium text-slate-800">{t(`opsWorkspace.${current[0]}`)}</span></span><a href="/launch-guide.html" className="text-xs text-indigo-600 hover:underline">{t('launchGuide.title')}</a><button className={`${opsButton} lg:hidden`} disabled={loading} onClick={() => void logout()}>{t('operations.logout')}</button></header>
      <main className="mx-auto max-w-[1500px] px-5 py-7 sm:px-8 sm:py-9"><div className="mb-7"><h1 className="text-2xl font-semibold tracking-tight">{t(`opsWorkspace.${current[0]}`)}</h1><p className="mt-2 text-sm leading-6 text-slate-500">{t(`opsWorkspace.${current[0]}Hint`)}</p></div>{error && <p role="alert" className="mb-5 rounded-lg bg-red-50 p-4 text-sm text-red-800">{error.startsWith('operations.') ? t(error) : error}</p>}
        <Suspense fallback={<p role="status" className="py-10 text-sm text-slate-500">{t('opsWorkspace.loading')}</p>}><Routes><Route index element={<Overview />} /><Route path="overview" element={<Navigate to="/ops" replace />} /><Route path="companies" element={<Directory />} /><Route path="companies/:companyId" element={<Detail />} /><Route path="models" element={<Models />} /><Route path="plans" element={<Sections kind="plans" />} /><Route path="finance" element={<Sections kind="finance" />} /><Route path="monitor" element={<Sections kind="monitor" />} /><Route path="audits" element={<Audits />} /><Route path="*" element={<Navigate to="/ops" replace />} /></Routes></Suspense>
      </main>
    </div>
  </div>;
}
