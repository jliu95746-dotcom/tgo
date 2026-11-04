import { useTranslation } from 'react-i18next';
import type { KnowledgeFile, KnowledgeGovernanceRecord } from '@/types';
import { getKnowledgeUseState } from '@/utils/knowledgeOnboarding';

export function KnowledgeDocumentUsage({ file, record, loaded, onConfigure }: {
  file: KnowledgeFile;
  record?: KnowledgeGovernanceRecord;
  loaded: boolean;
  onConfigure: () => void;
}) {
  const { t } = useTranslation();
  const state = getKnowledgeUseState(file, record, loaded);
  const canConfigure = loaded && file.processing_status === 'completed' && !!file.document_count;
  return <div className="min-w-0 space-y-1 break-words text-xs">
    <p className={state === 'authorized' ? 'text-green-700 dark:text-green-400' : 'text-amber-700 dark:text-amber-300'}>
      {t('knowledgeOnboarding.ai')}：{t(`knowledgeOnboarding.states.${state}`)}
    </p>
    {state === 'authorized' && <p className="text-gray-500 dark:text-gray-400">{record?.channels.map((channel) => t(`knowledgeOnboarding.channels.${channel}`)).join(' / ')}</p>}
    <button disabled={!canConfigure} type="button" onClick={onConfigure} className="rounded border border-blue-200 px-2 py-1 text-blue-600 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-blue-900 dark:text-blue-300 dark:hover:bg-blue-950/30">{t('knowledgeOnboarding.configure')}</button>
  </div>;
}
