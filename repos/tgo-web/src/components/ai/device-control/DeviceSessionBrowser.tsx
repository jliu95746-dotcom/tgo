import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, AlertCircle, Loader2, RefreshCw, X } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { SESSION_PAGE_SIZE, useDeviceSessionStore } from '@/stores/deviceSessionStore';
import type { Device } from '@/types/deviceControl';
import ComputerUseSessionMonitor from '../remote-agent/ComputerUseSessionMonitor';
import DeviceSessionStatus from './DeviceSessionStatus';
import SessionPagination from './SessionPagination';

interface Props { devices: Device[]; onClose: () => void }

export default function DeviceSessionBrowser({ devices, onClose }: Props) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const projectId = useAuthStore((state) => state.user?.project_id ?? null);
  const state = useDeviceSessionStore();
  const { reset, loadSessions } = state;

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    return () => { element?.close(); };
  }, []);

  useEffect(() => {
    reset(projectId);
    if (projectId) void loadSessions();
    return () => reset();
  }, [projectId, reset, loadSessions]);

  useEffect(() => {
    const timer = setInterval(() => {
      const current = useDeviceSessionStore.getState();
      if (!document.hidden && !current.selectedId && current.listPage === 0 &&
          !current.isListLoading && !current.listError) void current.loadSessions();
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  const scopeMatches = state.ownerProjectId === projectId && projectId !== null;
  const back = () => { void state.selectSession(null); void loadSessions(); };

  return <dialog ref={dialog} aria-labelledby="device-session-title"
    className="m-auto w-[calc(100vw-2rem)] max-w-5xl max-h-[90vh] rounded-2xl border border-gray-200 bg-white p-0 text-gray-900 shadow-2xl backdrop:bg-black/60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <header className="sticky top-0 z-10 flex items-center gap-3 border-b border-gray-200 bg-white px-5 py-4 dark:border-gray-700 dark:bg-gray-900">
      <Activity className="h-6 w-6 text-purple-500" />
      <div className="min-w-0 flex-1">
        <h2 id="device-session-title" className="text-lg font-semibold">{t('deviceControl.sessions.title')}</h2>
        <p className="text-xs text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.subtitle')}</p>
      </div>
      <button type="button" autoFocus onClick={onClose} aria-label={t('common.close')} className="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-5 w-5" /></button>
    </header>
    <div className="p-5">
      {!scopeMatches ? <p role="status">{t('common.loading')}</p> : state.selectedId ? (
        <ComputerUseSessionMonitor sessionId={state.selectedId} onClose={back} />
      ) : <>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <label className="flex min-w-0 items-center gap-2 text-sm">
            {t('deviceControl.sessions.device')}
            <select value={state.deviceId ?? ''} className="max-w-64 rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-800"
              onChange={(event) => void loadSessions(0, event.target.value || null)}>
              <option value="">{t('deviceControl.sessions.allDevices')}</option>
              {devices.map((device) => <option key={device.id} value={device.id}>{device.device_name}</option>)}
            </select>
          </label>
          <span className="flex-1 text-xs text-gray-500 dark:text-gray-400">{t(state.listError ? 'deviceControl.sessions.retryPaused' : state.listPage === 0 ? 'deviceControl.sessions.autoRefresh' : 'deviceControl.sessions.historyPaused')}</span>
          <button onClick={() => void loadSessions()} disabled={state.isListLoading} className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm disabled:opacity-40">
            <RefreshCw className={`h-4 w-4 ${state.isListLoading ? 'animate-spin' : ''}`} />{t('common.refresh')}
          </button>
        </div>
        {state.listError && <div role="alert" className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-200">
          <AlertCircle className="mr-2 inline h-4 w-4" />{state.listError}
          {state.sessions.length > 0 && <p>{t('deviceControl.sessions.stale')}</p>}
          <button className="mt-2 underline" onClick={() => void loadSessions()}>{t('common.retry')}</button>
        </div>}
        {state.isListLoading && state.sessions.length === 0 ? <div role="status" className="flex justify-center gap-2 py-12"><Loader2 className="h-5 w-5 animate-spin" />{t('common.loading')}</div> :
          state.sessions.length === 0 ? !state.listError && <p className="py-12 text-center text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.empty')}</p> :
          <ul className="space-y-2">{state.sessions.map((session) => <li key={session.id}>
            <button className="flex w-full flex-wrap items-center gap-3 rounded-xl border border-gray-200 p-4 text-left hover:border-purple-400 dark:border-gray-700"
              onClick={() => void state.selectSession(session.id)}>
              <div className="min-w-0 flex-1">
                <div className="break-words font-medium">{session.device_name} · {session.agent_name ?? t('deviceControl.sessions.unknownAgent')}</div>
                <time className="mt-1 block text-xs text-gray-500 dark:text-gray-400" dateTime={session.started_at}>{new Date(session.started_at).toLocaleString()}</time>
              </div>
              <span className="text-sm text-gray-500 dark:text-gray-400">{t('deviceControl.sessions.actionCount', { count: session.actions_count })}</span>
              <DeviceSessionStatus status={session.status} />
            </button>
          </li>)}</ul>}
        <SessionPagination page={state.listPage} total={state.total} pageSize={SESSION_PAGE_SIZE} loading={state.isListLoading} onChange={(page) => void loadSessions(page)} />
      </>}
    </div>
  </dialog>;
}
