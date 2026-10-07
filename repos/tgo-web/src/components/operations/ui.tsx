import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export const opsButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-indigo-500 disabled:cursor-not-allowed disabled:opacity-50';
export const opsPrimary = `${opsButton} !border-indigo-600 !bg-indigo-600 !text-white hover:!bg-indigo-700`;
export const opsInput = 'mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm focus-visible:outline-2 focus-visible:outline-indigo-500';

export function useOpsResource<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    let active = true;
    setBusy(true); setError(''); setData(null);
    void loader().then(value => { if (active) setData(value); })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : 'opsWorkspace.error'); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [loader, revision]);
  return { data, busy, error, reload };
}

export function OpsFeedback({ busy, error, reload }: { busy: boolean; error: string; reload: () => void }) {
  const { t } = useTranslation();
  if (error) return <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"><span>{error.startsWith('opsWorkspace.') ? t(error) : error}</span><button className={opsButton} onClick={reload}>{t('opsWorkspace.retry')}</button></div>;
  if (busy) return <p role="status" className="py-8 text-sm text-slate-500">{t('opsWorkspace.loading')}</p>;
  return null;
}

export function OpsPanel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-slate-200 bg-white p-5 sm:p-6 ${className}`}>{children}</section>;
}

export function OpsBadge({ state }: { state: string }) {
  const { t } = useTranslation();
  const tone = state === 'active' ? 'bg-emerald-50 text-emerald-800' : state === 'trial' ? 'bg-indigo-50 text-indigo-800' : state === 'expired' || state === 'suspended' ? 'bg-amber-50 text-amber-800' : 'bg-slate-100 text-slate-600';
  return <span className={`inline-flex whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>{t(`opsWorkspace.states.${state}`, { defaultValue: state })}</span>;
}

export function localDateTime(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
