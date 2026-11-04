import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { sessionCommandTransport } from '@/services/sessionCommandTransport';
import {
  checkPendingSessionCommand,
  listPendingSessionCommands,
  retryPendingSessionCommand,
  sessionCommandRecoveryChanged,
  sessionCommandResolved,
} from '@/utils/sessionCommandRecovery';
import type { PendingSessionCommand, SessionCommandScope } from '@/types/sessionCommand';

interface Props {
  onResolved: () => void;
}

export default function SessionCommandRecoveryNotice({ onResolved }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const projectId = user?.project_id;
  const actorId = user?.id;
  const [pending, setPending] = useState<PendingSessionCommand[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  const refresh = useCallback(() => {
    if (!projectId || !actorId) { setPending([]); return; }
    setPending(listPendingSessionCommands(localStorage, { projectId, actorId }));
  }, [projectId, actorId]);

  useEffect(() => {
    refresh();
    window.addEventListener(sessionCommandRecoveryChanged, refresh);
    window.addEventListener('storage', refresh);
    return () => {
      window.removeEventListener(sessionCommandRecoveryChanged, refresh);
      window.removeEventListener('storage', refresh);
    };
  }, [refresh]);

  const inspect = useCallback(async (command: PendingSessionCommand, retry: boolean) => {
    if (!projectId || !actorId || busyId) return;
    const scope: SessionCommandScope = { projectId, actorId };
    setBusyId(command.operationId);
    setMessage('');
    try {
      const result = retry
        ? await retryPendingSessionCommand(localStorage, scope, command.visitorId, sessionCommandTransport)
        : await checkPendingSessionCommand(localStorage, scope, command.visitorId, sessionCommandTransport);
      if (result?.state === 'committed') {
        setMessage(t('chat.header.commandCommitted', '原操作已完成'));
        window.dispatchEvent(new CustomEvent(sessionCommandResolved, {
          detail: { visitorId: command.visitorId, committed: true },
        }));
        onResolved();
      } else if (result?.state === 'rejected') {
        setMessage(result.message);
        window.dispatchEvent(new CustomEvent(sessionCommandResolved, {
          detail: { visitorId: command.visitorId, committed: false },
        }));
      } else {
        setMessage(t('chat.header.commandStillPending', '尚未查到原操作结果，请稍后查询。'));
      }
      refresh();
    } finally {
      setBusyId(null);
    }
  }, [projectId, actorId, busyId, onResolved, refresh, t]);

  if (pending.length === 0) return null;

  return (
    <section aria-label={t('chat.header.recoveryTitle', '待确认的会话操作')} className="max-h-32 shrink-0 overflow-y-auto border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <strong>{t('chat.header.recoveryTitle', '待确认的会话操作')} ({pending.length})</strong>
        {pending.map(command => (
          <div key={command.operationId} className="flex items-center gap-2">
            <span>{command.kind === 'close' ? t('chat.header.endChat', '结束聊天') : t('chat.header.transfer', '转接')} · {command.operationId.slice(0, 8)}</span>
            <button type="button" onClick={() => void inspect(command, false)} disabled={busyId !== null} className="rounded border border-amber-300 px-2 py-1 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:hover:bg-amber-900/30">
              {t('chat.header.checkCommand', '查询结果')}
            </button>
            <button type="button" onClick={() => void inspect(command, true)} disabled={busyId !== null} className="rounded border border-amber-300 px-2 py-1 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:hover:bg-amber-900/30">
              {t('chat.header.retryCommand', '重试原操作')}
            </button>
          </div>
        ))}
        {message && <span role="status">{message}</span>}
      </div>
    </section>
  );
}
