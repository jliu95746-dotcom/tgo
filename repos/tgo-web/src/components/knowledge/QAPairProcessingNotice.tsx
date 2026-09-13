import { useTranslation } from 'react-i18next';
import type { QAPairResponse } from '@/services/knowledgeBaseApi';

interface Props {
  status: QAPairResponse['status'];
  errorMessage?: string | null;
}

export default function QAPairProcessingNotice({ status, errorMessage }: Props) {
  const { t } = useTranslation();
  if (status !== 'failed') return null;
  return (
    <div className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-300" role="status">
      <p className="whitespace-pre-wrap break-words">
        {t('knowledge.qa.failureReason')}：{errorMessage || t('knowledge.qa.failureUnknown')}
      </p>
      <p className="mt-1">{t('knowledge.qa.retryHint')}</p>
    </div>
  );
}
