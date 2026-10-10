import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { KnowledgeAvailabilityApiService } from '@/services/knowledgeAvailabilityApi';
import type { AgentKnowledgeAvailability } from '@/types';

export function knowledgeAvailabilityStatus(report: AgentKnowledgeAvailability): 'ready' | 'empty' | 'blocked' | 'unbound' {
  if (report.collections.some(c => c.eligible_chunk_count > 0)) return 'ready';
  if (report.binding_mode === 'unbound') return 'unbound';
  return report.collections.length || report.issues.length ? 'blocked' : 'empty';
}

export default function KnowledgeAvailabilitySummary({ platformId, agentId }: { platformId: string; agentId?: string }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<AgentKnowledgeAvailability | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    setReport(null);
    setError(false);
    void KnowledgeAvailabilityApiService.getForPlatform(platformId, agentId).then(
      result => { if (active) setReport(result); },
      () => { if (active) setError(true); },
    );
    return () => { active = false; };
  }, [platformId, agentId]);

  if (!report && !error) return <p className="mt-2 text-xs text-gray-500">{t('knowledge.availability.loading')}</p>;
  if (error) return <p role="alert" className="mt-2 text-xs text-amber-700">{t('knowledge.availability.loadFailed')}</p>;
  if (!report) return null;
  const status = knowledgeAvailabilityStatus(report);
  const availableCount = report.collections.filter(c => c.eligible_chunk_count > 0).length;
  return (
    <div className={`mt-3 rounded-lg p-3 text-xs ${status === 'ready' ? 'bg-green-50 text-green-800 dark:bg-green-950/30 dark:text-green-300' : 'bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300'}`}>
      <p>{t(`knowledge.availability.${status}`, { count: availableCount })}</p>
      <p className="mt-1">{t(`knowledge.availability.mode.${report.binding_mode}`)}</p>
      {report.issues.filter(issue => issue !== 'no_binding').map(issue => (
        <p key={issue} className="mt-1">{t(`knowledge.availability.issues.${issue}`, { defaultValue: t('knowledge.availability.blockedReason') })}</p>
      ))}
      {report.collections.filter(c => c.eligible_chunk_count === 0).map(collection => (
        <p key={collection.id} className="mt-1">
          <Link className="underline" to={`/knowledge/${collection.id}`}>{collection.name}</Link>
          {'：'}{collection.blocked_reasons.length
            ? collection.blocked_reasons.map(reason => t(`knowledge.availability.reasons.${reason}`, { defaultValue: t('knowledge.availability.blockedReason') })).join('、')
            : t('knowledge.availability.noReadyDocuments')}
        </p>
      ))}
      <Link className="mt-2 inline-block underline" to="/knowledge">{t('knowledge.availability.manage')}</Link>
    </div>
  );
}
