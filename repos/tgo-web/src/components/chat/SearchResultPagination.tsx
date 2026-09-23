import { useTranslation } from 'react-i18next';
import type { MessageSearchPagination } from '@/types';

interface Props {
  pagination: MessageSearchPagination | null;
  onPageChange: (page: number) => void;
}

export default function SearchResultPagination({ pagination, onPageChange }: Props) {
  const { t } = useTranslation();
  if (!pagination || (!pagination.has_previous && !pagination.has_next)) return null;
  const buttonClass = 'rounded border border-gray-300 px-3 py-1 text-sm dark:border-gray-600 disabled:opacity-40';
  return (
    <div className="mt-2 flex items-center gap-3">
      <button className={buttonClass} disabled={!pagination.has_previous} onClick={() => onPageChange(pagination.page - 1)}>
        {t('visitor.pagination.previous', '上一页')}
      </button>
      <span className="text-sm text-gray-500 dark:text-gray-400" aria-current="page">{pagination.page}</span>
      <button className={buttonClass} disabled={!pagination.has_next} onClick={() => onPageChange(pagination.page + 1)}>
        {t('visitor.pagination.next', '下一页')}
      </button>
    </div>
  );
}
