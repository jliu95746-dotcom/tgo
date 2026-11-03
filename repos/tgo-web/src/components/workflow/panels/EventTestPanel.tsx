import { useEffect, useId } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import { CheckCircle2, Loader2, Play, RefreshCw, RotateCcw, StopCircle, X, Zap } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowEventTestStore, workflowEventOwner } from '@/stores/workflowEventTestStore';
import { eventTestTerminal } from '@/stores/workflowEventTestActions';
import EventSourceConfiguration from './EventSourceConfiguration';

export default function EventTestPanel({ workflowId, onClose }: {
  workflowId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const inputId = useId();
  const ownerKey = useAuthStore(state => workflowEventOwner(state.user));
  const store = useWorkflowEventTestStore();
  const { activate } = store;
  useEffect(() => {
    if (ownerKey) return activate(ownerKey, workflowId);
    return undefined;
  }, [activate, ownerKey, workflowId]);

  // Hide retained data immediately on an account change, before effect cleanup runs.
  const session = ownerKey && store.ownerKey === ownerKey && store.activeWorkflowId === workflowId
    ? store.sessions[workflowId] : undefined;
  const context = session?.context;
  const record = session?.execution;
  const busy = Boolean(session?.loadingContext || session?.sending || session?.refreshing || session?.cancelling);
  const terminal = eventTestTerminal(record?.status);
  const mayReset = Boolean(session && !busy && (!session.attempt || session.rejected || terminal));
  const currentStatus = record?.status || session?.accepted?.status || 'unknown';
  const statusLabel = (status: string) => t(`workflowEvents.status.${status}`, { defaultValue: t('workflowEvents.status.unknown') });
  const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800';

  return (
    <div role="region" aria-label={t('workflowEvents.title')} className="absolute inset-0 z-40 flex w-full flex-col border-l border-gray-100 bg-white shadow-2xl sm:relative sm:inset-auto sm:w-[22rem] sm:shrink-0 dark:border-gray-800 dark:bg-gray-900">
      <div className="flex items-center justify-between border-b border-gray-50 px-6 py-6 dark:border-gray-800">
        <div className="flex items-center gap-3">
          <div className="rounded-2xl border border-orange-100 bg-orange-50 p-2.5 text-orange-600 dark:border-orange-800/50 dark:bg-orange-900/20 dark:text-orange-400"><Zap className="h-5 w-5" /></div>
          <div><h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">{t('workflowEvents.title')}</h3>
            <p className="mt-1 text-[10px] text-gray-500">{t('workflowEvents.subtitle')}</p></div>
        </div>
        <button type="button" onClick={onClose} aria-label={t('businessScenarios.workflow.closeTest')} className="rounded-xl p-2 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-5 w-5" /></button>
      </div>
      <div className="custom-scrollbar flex-1 space-y-6 overflow-y-auto p-6">
        {!ownerKey ? <p role="status" className="text-sm text-gray-600 dark:text-gray-300">{t('workflowEvents.adminOnly')}</p> : <>
          {session?.loadingContext && <p role="status" className="flex items-center gap-2 text-xs text-gray-500"><Loader2 className="h-4 w-4 animate-spin" />{t('workflowEvents.loadingContext')}</p>}
          {context && <section className="space-y-3 rounded-2xl border border-purple-100 bg-purple-50/50 p-4 dark:border-purple-900/50 dark:bg-purple-900/10">
            <dl className="space-y-2 text-xs">
              <div className="flex items-center justify-between"><dt className="text-gray-500">{t('workflowEvents.version')}</dt><dd className="font-bold text-purple-600 dark:text-purple-400">v{context.version}</dd></div>
              <div><dt className="text-gray-500">{t('workflowEvents.eventType')}</dt><dd className="mt-1 break-all font-mono text-gray-700 dark:text-gray-200">{context.event_type}</dd></div>
            </dl>
            <p className="text-xs leading-5 text-purple-700 dark:text-purple-300">{t('workflowEvents.publishedHint', { version: context.version })}</p>
            <p className="text-xs leading-5 text-gray-500">{t('workflowEvents.draftHint')}</p>
          </section>}
          <EventSourceConfiguration workflowId={workflowId} />
          <p className="text-xs leading-5 text-gray-500">{t('workflowEvents.scopeHint')}</p>
          {!session?.attempt && <button type="button" onClick={() => void store.reloadContext()} disabled={busy || !session} className={secondaryButton}><RefreshCw className="h-3.5 w-3.5" />{t('workflowEvents.reloadContext')}</button>}
          <section className="space-y-3">
            <label htmlFor={inputId} className="block text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowEvents.dataLabel')}</label>
            <textarea id={inputId} aria-describedby={`${inputId}-help`} spellCheck={false} rows={7}
              value={session?.jsonInput || ''} onChange={event => store.setInput(event.target.value)}
              disabled={!session || Boolean(session.attempt) || busy}
              className="w-full resize-y rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 font-mono text-xs leading-5 text-gray-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-100" />
            <p id={`${inputId}-help`} className="text-xs leading-5 text-gray-500">{t('workflowEvents.dataHelp')}</p>
            {session?.attempt && <div className="space-y-2 rounded-xl bg-gray-50 p-3 text-xs leading-5 text-gray-500 dark:bg-gray-800/50">
              <p>{t('workflowEvents.locked')}</p>
              <p className="break-all">{t('workflowEvents.requestId')}：<span className="font-mono">{session.attempt.request_id}</span></p>
            </div>}
            {session?.error && <p role="alert" className="break-words rounded-xl bg-red-50 p-3 text-xs leading-5 text-red-600 dark:bg-red-900/20 dark:text-red-400">{session.error}</p>}
            {session?.attempt && !session.accepted && !session.sending && <p role="status" className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t(session.rejected ? 'workflowEvents.rejected' : 'workflowEvents.uncertain')}</p>}
            {!session?.accepted && !session?.rejected && <button type="button" onClick={() => void store.send()} disabled={!context || busy}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-blue-600 px-4 py-3 text-sm font-bold text-white shadow-lg shadow-blue-500/20 transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40">
              {session?.sending ? <Loader2 className="h-4 w-4 animate-spin" /> : session?.attempt ? <RefreshCw className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {t(session?.sending ? 'workflowEvents.sending' : session?.attempt ? 'workflowEvents.retry' : 'workflowEvents.send')}
            </button>}
            <button type="button" onClick={store.reset} disabled={!mayReset} className={secondaryButton}><RotateCcw className="h-3.5 w-3.5" />{t('workflowEvents.reset')}</button>
          </section>
          {session?.accepted && <section className="space-y-3 rounded-2xl border border-gray-100 bg-gray-50/50 p-4 dark:border-gray-700 dark:bg-gray-800/30">
            <p role="status" className="flex items-center gap-2 text-sm font-semibold text-gray-800 dark:text-gray-100">
              {!terminal ? <Loader2 className="h-4 w-4 animate-spin text-blue-500" /> : record?.status === 'completed' ? <CheckCircle2 className="h-4 w-4 text-green-500" /> : <StopCircle className="h-4 w-4 text-gray-400" />}{statusLabel(currentStatus)}
            </p>
            <p className="text-xs leading-5 text-gray-500">{t(session.accepted.deduplicated ? 'workflowEvents.deduplicated' : 'workflowEvents.accepted')}</p>
            <p className="break-all text-xs text-gray-500">{t('workflowEvents.runId')}：<span className="font-mono">{session.accepted.execution_id}</span></p>
            {record?.error && <p role="alert" className="break-words text-xs leading-5 text-red-600 dark:text-red-400">{record.error}</p>}
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => void store.refresh()} disabled={busy} className={secondaryButton}><RefreshCw className={`h-3.5 w-3.5 ${session.refreshing ? 'animate-spin' : ''}`} />{t('workflowEvents.refresh')}</button>
              {!terminal && <button type="button" onClick={() => void store.cancel()} disabled={busy} className={secondaryButton}><StopCircle className="h-3.5 w-3.5" />{t(session.cancelling ? 'workflowEvents.cancelling' : 'workflowEvents.cancel')}</button>}
            </div>
          </section>}
          {record && record.node_executions.length > 0 && <section className="space-y-3">
            <h4 className="text-xs font-semibold text-gray-500">{t('workflowEvents.steps')}</h4>
            {record.node_executions.map(node => <details key={node.id} className="rounded-xl border border-gray-100 p-3 text-xs dark:border-gray-700">
              <summary className="cursor-pointer text-gray-700 dark:text-gray-200">{t(`workflow.node_types.${node.node_type}.label`, { defaultValue: node.node_type })} · {statusLabel(node.status)}</summary>
              {node.input != null && <div className="mt-3"><p className="text-gray-500">{t('workflowEvents.input')}</p><pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words text-gray-700 dark:text-gray-300">{JSON.stringify(node.input, null, 2)}</pre></div>}
              {node.output != null && <div className="mt-3"><p className="text-gray-500">{t('workflowEvents.output')}</p><pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words text-gray-700 dark:text-gray-300">{JSON.stringify(node.output, null, 2)}</pre></div>}
              {node.error && <p className="mt-3 break-words text-red-600 dark:text-red-400">{node.error}</p>}
            </details>)}
          </section>}
          {record?.status === 'completed' && record.output != null && <section className="space-y-3">
            <h4 className="flex items-center gap-2 text-xs font-semibold text-gray-500"><CheckCircle2 className="h-4 w-4 text-green-500" />{t('workflowEvents.finalOutput')}</h4>
            <div className="rounded-2xl border border-green-100 bg-green-50/50 p-4 dark:border-green-900/30 dark:bg-green-900/10">
              {typeof record.output === 'string' ? <div className="break-words text-sm leading-6 text-green-700 dark:text-green-400 [&_p+p]:mt-3 [&_strong]:font-semibold"><ReactMarkdown>{record.output}</ReactMarkdown></div>
                : <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-green-700 dark:text-green-400">{JSON.stringify(record.output, null, 2)}</pre>}
            </div>
          </section>}
        </>}
      </div>
      {session?.accepted && <p className="border-t border-gray-100 bg-blue-50/30 p-4 text-[11px] leading-5 text-gray-500 dark:border-gray-800 dark:bg-blue-900/5">{t('workflowEvents.observeHint')}</p>}
    </div>
  );
}
