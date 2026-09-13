import { useTranslation } from 'react-i18next';

interface Props {
  page: number;
  total: number;
  pageSize: number;
  loading: boolean;
  onChange: (page: number) => void;
}

export default function SessionPagination({ page, total, pageSize, loading, onChange }: Props) {
  const { t } = useTranslation();
  if (total <= pageSize && page === 0) return null;
  return <nav className="flex flex-wrap items-center justify-between gap-3 pt-4 text-sm" aria-label={t('deviceControl.sessions.pagination')}>
    <button className="rounded-lg border px-3 py-2 disabled:opacity-40" disabled={loading || page === 0} onClick={() => onChange(page - 1)}>
      {t('deviceControl.sessions.previous')}
    </button>
    <span>{t('deviceControl.sessions.page', { page: page + 1, total })}</span>
    <button className="rounded-lg border px-3 py-2 disabled:opacity-40" disabled={loading || (page + 1) * pageSize >= total} onClick={() => onChange(page + 1)}>
      {t('deviceControl.sessions.next')}
    </button>
  </nav>;
}
