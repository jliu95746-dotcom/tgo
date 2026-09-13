import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KnowledgeBaseApiService } from '@/services/knowledgeBaseApi';
import type { KnowledgeGovernanceSource } from '@/types';
import { KnowledgeGovernancePanel } from './KnowledgeGovernancePanel';

interface Props {
  collectionId: string;
  collectionName: string;
  sourceType: 'qa' | 'website';
}

/** Load sources independently of the content list's filters and lazy tree. */
export function SourceGovernanceSection({ collectionId, collectionName, sourceType }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<KnowledgeGovernanceSource[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items: KnowledgeGovernanceSource[] = [];
      let offset = 0;
      let hasNext = true;
      while (hasNext) {
        if (sourceType === 'qa') {
          const response = await KnowledgeBaseApiService.getQAPairs(collectionId, { limit: 100, offset });
          items.push(...response.data.map(pair => ({ id: pair.id, name: pair.question })));
          hasNext = offset + response.data.length < response.total && response.data.length > 0;
          offset += response.data.length;
        } else {
          const response = await KnowledgeBaseApiService.getFiles({ collection_id: collectionId, limit: 100, offset });
          items.push(...response.data.map(file => ({ id: file.id, name: file.original_filename })));
          hasNext = response.pagination.has_next && response.data.length > 0;
          offset += response.data.length;
        }
      }
      setSources(items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('knowledge.governance.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [collectionId, sourceType, t]);

  useEffect(() => { if (open) void load(); }, [open, load]);

  return (
    <section className="my-4 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="w-full px-5 py-3 text-left font-medium text-gray-900 dark:text-gray-100">
        {t('knowledge.governance.title')} {open ? '−' : '+'}
      </button>
      {open && <>
        <p className="px-5 pb-2 text-sm text-gray-500">{t('knowledge.governance.sourceReviewHint')}</p>
        {error && <p role="alert" className="px-5 text-red-500">{error}</p>}
        {loading && <p className="px-5 text-gray-500">{t('common.loading')}</p>}
        <KnowledgeGovernancePanel collectionId={collectionId} collectionName={collectionName}
          sourceType={sourceType} documents={sources} onRefresh={load} />
      </>}
    </section>
  );
}
