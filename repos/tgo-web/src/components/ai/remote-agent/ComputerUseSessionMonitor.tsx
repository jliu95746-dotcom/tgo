import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react';
import { STEP_PAGE_SIZE, useDeviceSessionStore } from '@/stores/deviceSessionStore';
import DeviceSessionStatus from '../device-control/DeviceSessionStatus';
import SessionPagination from '../device-control/SessionPagination';

interface Props {
  sessionId: string;
  onClose: () => void;
  autoRefresh?: boolean;
  refreshInterval?: number;
}

export function ComputerUseSessionMonitor({ sessionId, onClose, autoRefresh = true, refreshInterval = 2000 }: Props) {
  const { t } = useTranslation();
  const state = useDeviceSessionStore();
  const { selectSession, loadDetail } = state;
  const session = state.selectedId === sessionId ? state.detail : null;

  useEffect(() => {
    if (useDeviceSessionStore.getState().selectedId !== sessionId) void selectSession(sessionId);
  }, [sessionId, selectSession]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(() => {
      const current = useDeviceSessionStore.getState();
      if (!document.hidden && current.selectedId === sessionId && current.detail?.status === 'running' &&
          current.stepPage === 0 && !current.isDetailLoading && !current.detailError) void current.loadDetail();
    }, Math.max(2000, refreshInterval));
    return () => clearInterval(timer);
  }, [sessionId, autoRefresh, refreshInterval]);

  return <section aria-label={t('deviceControl.sessions.detailTitle')} className="space-y-4">
    <div className="flex items-center justify-between gap-3">
      <button onClick={onClose} className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"><ArrowLeft className="h-4 w-4" />{t('deviceControl.sessions.back')}</button>
      <button onClick={() => void loadDetail()} disabled={state.isDetailLoading} className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm disabled:opacity-40">
        <RefreshCw className={state.isDetailLoading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />{t('common.refresh')}
      </button>
    </div>
    {state.detailError && <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-200">
      <p>{state.detailError}</p>{session && <p>{t('deviceControl.sessions.stale')}</p>}
      <button className="mt-2 underline" onClick={() => void loadDetail()}>{t('common.retry')}</button>
    </div>}
    {state.isDetailLoading && !session && <p role="status" className="flex items-center justify-center gap-2 py-12"><Loader2 className="h-5 w-5 animate-spin" />{t('common.loading')}</p>}
    {session && <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="break-words text-lg font-semibold">{session.device_name} · {session.agent_name ?? t('deviceControl.sessions.unknownAgent')}</h3>
          <p className="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.executionId')}：{session.id}</p>
        </div>
        <DeviceSessionStatus status={session.status} />
      </div>
      <dl className="grid grid-cols-3 gap-3 rounded-xl bg-gray-50 p-4 text-center dark:bg-gray-800">
        <div><dt className="text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.actions')}</dt><dd className="mt-1 text-xl font-semibold">{session.actions_count}</dd></div>
        <div><dt className="text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.failedActions')}</dt><dd className="mt-1 text-xl font-semibold">{session.failed_actions_count}</dd></div>
        <div><dt className="text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.screenshots')}</dt><dd className="mt-1 text-xl font-semibold">{session.screenshots_count}</dd></div>
      </dl>
      <p className="text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.privacy')}</p>
      {session.status === 'interrupted' && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">{t('deviceControl.sessions.interruptedHint')}</p>}
      <div className="flex flex-wrap gap-3 text-xs text-gray-500 dark:text-gray-400">
        <span>{t('deviceControl.sessions.started')}：<time dateTime={session.started_at}>{new Date(session.started_at).toLocaleString()}</time></span>
        {session.ended_at && <span>{t(session.status === 'interrupted' ? 'deviceControl.sessions.recordEnded' : 'deviceControl.sessions.ended')}：<time dateTime={session.ended_at}>{new Date(session.ended_at).toLocaleString()}</time></span>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-medium">{t('deviceControl.sessions.steps')}</h4>
        <span className="text-xs text-gray-500 dark:text-gray-400">{t(state.detailError ? 'deviceControl.sessions.retryPaused' : state.stepPage > 0 ? 'deviceControl.sessions.historyPaused' : session.status === 'running' ? 'deviceControl.sessions.autoRefresh' : 'deviceControl.sessions.finishedRefresh')}</span>
      </div>
      {session.steps.length === 0 ? <p className="py-8 text-center text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.noSteps')}</p> :
        <ol className="space-y-2">{session.steps.map((step) => <li key={step.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
          <div className="min-w-0 flex-1"><p className="break-all text-sm font-medium">{step.tool_name}</p>
            <time className="text-xs text-gray-500 dark:text-gray-400" dateTime={step.started_at}>{new Date(step.started_at).toLocaleString()}</time>
            {step.ended_at && <p className="text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.ended')}：{new Date(step.ended_at).toLocaleString()}</p>}
          </div>
          <DeviceSessionStatus status={step.status} />
        </li>)}</ol>}
      <SessionPagination page={state.stepPage} total={session.step_total} pageSize={STEP_PAGE_SIZE} loading={state.isDetailLoading} onChange={(page) => void loadDetail(page)} />
    </>}
  </section>;
}

export default ComputerUseSessionMonitor;
