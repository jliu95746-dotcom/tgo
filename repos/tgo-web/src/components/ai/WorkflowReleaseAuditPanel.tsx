import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LuChevronLeft, LuChevronRight, LuRefreshCw } from 'react-icons/lu';
import { AIAgentsApiService } from '@/services/aiAgentsApi';
import { useAuthStore } from '@/stores/authStore';
import type { WorkflowReleaseAuditResponse } from '@/types/workflowReleaseAudit';
import LegacyWorkflowPinAction from './LegacyWorkflowPinAction';

interface Props {
  identity: string;
  revision: number;
  onReview: (agentId: string) => void;
  onChanged: () => void;
}

const pageSize = 10;

export default function WorkflowReleaseAuditPanel({ identity, revision, onReview, onChanged }: Props) {
  const { t } = useTranslation();
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [report, setReport] = useState<WorkflowReleaseAuditResponse | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const owns = () => {
      const user = useAuthStore.getState().user;
      return user?.role === 'admin' && `${user.project_id}:${user.id}` === identity;
    };
    setReport(null);
    setError(false);
    setLoading(true);
    void AIAgentsApiService.getWorkflowReleaseAudit(offset, pageSize).then(response => {
      if (cancelled || !owns()) return;
      if (offset >= response.pagination.total && offset > 0) {
        setOffset(Math.max(0, Math.floor((response.pagination.total - 1) / pageSize) * pageSize));
      } else {
        setReport(response);
      }
    }).catch(() => {
      if (!cancelled && owns()) setError(true);
    }).finally(() => {
      if (!cancelled && owns()) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [identity, offset, refresh, revision]);

  if (!error && (!report || report.pagination.total === 0)) return null;

  return (
    <section className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-900 dark:bg-amber-950/20" aria-label={t('workflowRelease.audit.title')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-amber-900 dark:text-amber-200">{t('workflowRelease.audit.title')}</h3>
          {report && <p className="mt-1 text-sm text-amber-800 dark:text-amber-300">{t('workflowRelease.audit.intro', { count: report.pagination.total })}</p>}
        </div>
        <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-amber-900 hover:bg-amber-100 disabled:opacity-50 dark:text-amber-200 dark:hover:bg-amber-900/30">
          <LuRefreshCw size={15} />{t('workflowRelease.audit.refresh')}
        </button>
      </div>
      {error ? (
        <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{t('workflowRelease.audit.loadFailed')}</p>
      ) : (
        <>
          <div className="mt-3 space-y-2">
            {report?.data.map(item => (
              <div key={`${item.agent_id}:${item.workflow_id}`} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-100 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                <div className="min-w-0 text-sm text-gray-700 dark:text-gray-300">
                  <div className="font-medium text-gray-900 dark:text-gray-100">{item.agent_name}{item.agent_active && <span className="ml-2 text-amber-700 dark:text-amber-300">{t('workflowRelease.audit.active')}</span>}</div>
                  <div className="truncate">{item.workflow_name || item.workflow_id} · {item.current_published_version
                    ? t('workflowRelease.audit.candidate', { version: item.current_published_version })
                    : t('workflowRelease.audit.unavailable')}</div>
                </div>
                <button type="button" onClick={() => onReview(item.agent_id)} className="shrink-0 rounded-lg border border-blue-200 px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50 dark:border-blue-900 dark:text-blue-300 dark:hover:bg-blue-950/40">
                  {t('workflowRelease.audit.review')}
                </button>
                <div className="w-full">
                  <LegacyWorkflowPinAction item={item} identity={identity} onChanged={() => {
                    setRefresh(value => value + 1);
                    onChanged();
                  }} />
                </div>
              </div>
            ))}
          </div>
          {report && (report.pagination.has_prev || report.pagination.has_next) && (
            <div className="mt-3 flex items-center justify-end gap-2 text-sm text-gray-600 dark:text-gray-300">
              <span>{t('workflowRelease.audit.range', { start: offset + 1, end: Math.min(offset + pageSize, report.pagination.total), total: report.pagination.total })}</span>
              <button type="button" aria-label={t('workflowRelease.audit.previous')} disabled={!report.pagination.has_prev || loading} onClick={() => setOffset(value => Math.max(0, value - pageSize))} className="rounded-lg p-1 hover:bg-amber-100 disabled:opacity-40 dark:hover:bg-amber-900/30"><LuChevronLeft size={18} /></button>
              <button type="button" aria-label={t('workflowRelease.audit.next')} disabled={!report.pagination.has_next || loading} onClick={() => setOffset(value => value + pageSize)} className="rounded-lg p-1 hover:bg-amber-100 disabled:opacity-40 dark:hover:bg-amber-900/30"><LuChevronRight size={18} /></button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
