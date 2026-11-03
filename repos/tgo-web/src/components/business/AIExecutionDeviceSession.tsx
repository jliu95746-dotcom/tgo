import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import DeviceSessionStatus from '@/components/ai/device-control/DeviceSessionStatus';
import { APIError } from '@/services/api';
import { getDeviceSession } from '@/services/deviceControlApi';
import type { DeviceSessionDetail } from '@/types/deviceControl';

const STEP_PAGE_SIZE = 20;

export default function AIExecutionDeviceSession({ executionId }: { executionId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [reload, setReload] = useState(0);
  const [session, setSession] = useState<DeviceSessionDetail | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading');
  const label = 'businessScenarios.workflow.deviceExecution';

  useEffect(() => {
    const controller = new AbortController();
    setSession(null);
    setState('loading');
    getDeviceSession(executionId, {
      step_skip: page * STEP_PAGE_SIZE,
      step_limit: STEP_PAGE_SIZE,
    }, { signal: controller.signal })
      .then(detail => {
        if (controller.signal.aborted) return;
        if (detail.id !== executionId) {
          setState('error');
          return;
        }
        setSession(detail);
        setState('ready');
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setState(error instanceof APIError && error.status === 404 ? 'none' : 'error');
      });
    return () => controller.abort();
  }, [executionId, page, reload]);

  return <section className="mt-3 space-y-2 border-l-2 border-purple-200 pl-3 text-xs dark:border-purple-800">
    <p className="font-medium text-gray-700 dark:text-gray-200">{t(`${label}.title`)}</p>
    {state === 'loading' && <p role="status" className="text-gray-500">{t(`${label}.loading`)}</p>}
    {state === 'none' && <p className="text-gray-500 dark:text-gray-400">{t(`${label}.none`)}</p>}
    {state === 'error' && <p role="alert" className="text-red-600 dark:text-red-300">{t(`${label}.failedLoad`)}</p>}
    {state === 'ready' && session && <>
      <div className="flex flex-wrap items-center gap-2 text-gray-700 dark:text-gray-200">
        <span>{session.device_name}</span>
        <DeviceSessionStatus status={session.status} />
      </div>
      <p className="text-gray-500 dark:text-gray-400">
        {t(`${label}.counts`, {
          actions: session.actions_count,
          failed: session.failed_actions_count,
        })}
      </p>
      {session.steps.length === 0 && <p className="text-gray-500 dark:text-gray-400">{t(`${label}.noSteps`)}</p>}
      {session.steps.length > 0 && <ol className="space-y-2">{session.steps.map(step => <li key={step.id} className="flex flex-wrap items-center gap-2 text-gray-600 dark:text-gray-300">
        <span className="break-all">{step.tool_name}</span>
        <DeviceSessionStatus status={step.status} />
        <time className="text-gray-500" dateTime={step.started_at}>{new Date(step.started_at).toLocaleString()}</time>
      </li>)}</ol>}
      {session.step_total > STEP_PAGE_SIZE && <div className="flex items-center gap-3 text-blue-600 dark:text-blue-300">
        <button type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)} className="disabled:opacity-40">{t(`${label}.previous`)}</button>
        <span className="text-gray-500">{t(`${label}.page`, { current: page + 1, total: Math.ceil(session.step_total / STEP_PAGE_SIZE) })}</span>
        <button type="button" disabled={(page + 1) * STEP_PAGE_SIZE >= session.step_total} onClick={() => setPage(value => value + 1)} className="disabled:opacity-40">{t(`${label}.next`)}</button>
      </div>}
    </>}
    <button type="button" disabled={state === 'loading'} onClick={() => setReload(value => value + 1)} className="text-blue-600 disabled:opacity-40 dark:text-blue-300">
      {t(`${label}.refresh`)}
    </button>
    <p className="text-gray-500 dark:text-gray-400">{t(`${label}.hint`)}</p>
  </section>;
}
