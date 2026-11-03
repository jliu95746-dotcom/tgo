import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import DeviceSessionStatus from '@/components/ai/device-control/DeviceSessionStatus';
import { listDeviceSessions } from '@/services/deviceControlApi';
import type { DeviceSession } from '@/types/deviceControl';
import AIExecutionDeviceSession from './AIExecutionDeviceSession';

export default function WorkflowDeviceSessions({ workflowExecutionId, projectId }: {
  workflowExecutionId: string;
  projectId: string | undefined;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [reload, setReload] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [result, setResult] = useState<{
    projectId: string;
    workflowExecutionId: string;
    sessions: DeviceSession[];
    total: number;
  } | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');

  useEffect(() => {
    if (!expanded || !projectId) return;
    const controller = new AbortController();
    setState('loading');
    listDeviceSessions({ workflow_execution_id: workflowExecutionId, limit: 20 }, {
      signal: controller.signal,
    }).then(response => {
      if (controller.signal.aborted) return;
      setResult({ projectId, workflowExecutionId, ...response });
      setState('ready');
    }).catch(() => {
      if (!controller.signal.aborted) setState('error');
    });
    return () => controller.abort();
  }, [expanded, projectId, workflowExecutionId, reload]);

  const current = result?.projectId === projectId && result?.workflowExecutionId === workflowExecutionId ? result : null;
  return <div className="mt-3 border-l-2 border-purple-200 pl-3 text-xs dark:border-purple-800">
    <button type="button" onClick={() => setExpanded(value => !value)} className="text-blue-600 hover:underline dark:text-blue-300">
      {t(`businessScenarios.workflow.deviceExecution.${expanded ? 'relatedHide' : 'relatedShow'}`)}
    </button>
    {expanded && <div className="mt-2 space-y-2">
      {state === 'loading' && <p role="status" className="text-gray-500">{t('common.loading')}</p>}
      {state === 'error' && <p role="alert" className="text-red-600">{t('deviceControl.sessions.loadFailed')}</p>}
      {state === 'ready' && current && <>
        {current.total === 0 && <p className="text-gray-500">{t('businessScenarios.workflow.deviceExecution.relatedNone')}</p>}
        {current.sessions.map(session => <div key={session.id} className="space-y-1 rounded-lg border border-gray-200 p-2 dark:border-gray-700">
          <button type="button" onClick={() => setSelectedId(value => value === session.id ? null : session.id)} className="flex w-full flex-wrap items-center gap-2 text-left text-blue-600 dark:text-blue-300">
            <span>{session.device_name}</span>
            <DeviceSessionStatus status={session.status} />
            <span>{t('deviceControl.sessions.actionCount', { count: session.actions_count })}</span>
          </button>
          <p className="break-all text-gray-500">{session.id}</p>
          {selectedId === session.id && <AIExecutionDeviceSession executionId={session.id} />}
        </div>)}
        {current.total > current.sessions.length && <p className="text-gray-500">{t('businessScenarios.workflow.deviceExecution.moreRecords', { count: current.total - current.sessions.length })}</p>}
      </>}
      <button type="button" disabled={state === 'loading'} onClick={() => setReload(value => value + 1)} className="text-blue-600 disabled:opacity-40 dark:text-blue-300">
        {t('common.refresh')}
      </button>
    </div>}
  </div>;
}
