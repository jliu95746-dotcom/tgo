import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import { RefreshCw } from 'lucide-react';

import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import type {
  WorkflowExecution, WorkflowReconciliationSummary,
} from '@/types/workflow';
import { reconciliationDetailsMatch, reconciliationOutput } from '@/utils/workflowReconciliation';

const copy = 'workflowReconciliation';
type Decision = 'continue' | 'stop';
type Preview = {
  review: WorkflowReconciliationSummary;
  decision: Decision;
  execution: WorkflowExecution | null;
};

function ReconciliationInbox({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<WorkflowReconciliationSummary[]>([]);
  const [cursor, setCursor] = useState<WorkflowReconciliationSummary | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const busyRef = useRef(false);
  const listRequest = useRef(0);
  const previewPanel = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!preview) return;
    previewPanel.current?.focus({ preventScroll: true });
    previewPanel.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [preview]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; listRequest.current += 1; };
  }, []);

  useEffect(() => {
    const request = ++listRequest.current;
    let active = true;
    setLoading(true);
    setItems([]);
    setCursor(null);
    setHasMore(false);
    setPreview(null);
    setError('');
    WorkflowApiService.listReconciliations().then(rows => {
      if (!active || listRequest.current !== request) return;
      setItems(rows);
      setCursor(rows.length ? rows[rows.length - 1] : null);
      setHasMore(rows.length === 30);
    }).catch(() => {
      if (active) setError(t(`${copy}.loadError`));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshToken, reload, t]);

  const loadMore = async () => {
    if (loading || !cursor || !hasMore) return;
    const request = listRequest.current;
    setLoading(true);
    setError('');
    try {
      const rows = await WorkflowApiService.listReconciliations(cursor);
      if (!mounted.current || request !== listRequest.current) return;
      setItems(current => [...current, ...rows.filter(row => !current.some(old => old.id === row.id))]);
      setCursor(rows.length ? rows[rows.length - 1] : null);
      setHasMore(rows.length === 30);
    } catch {
      if (mounted.current && request === listRequest.current) setError(t(`${copy}.loadError`));
    } finally {
      if (mounted.current && request === listRequest.current) setLoading(false);
    }
  };

  const showPreview = async (item: WorkflowReconciliationSummary, decision: Decision) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setMessage('');
    setPreview(null);
    const request = listRequest.current;
    try {
      const review = await WorkflowApiService.getReconciliation(item.id);
      if (review.id !== item.id || review.execution_id !== item.execution_id
        || review.workflow_id !== item.workflow_id || !/^[0-9a-f]{64}$/.test(review.review_digest)) {
        throw new Error('Reconciliation preview scope mismatch');
      }
      const execution = await WorkflowApiService.getExecution(review.execution_id).catch(() => null);
      if (!mounted.current || listRequest.current !== request) return;
      setPreview({ review, decision, execution });
    } catch {
      if (mounted.current) setError(t(`${copy}.previewError`));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const decide = async () => {
    if (!preview || busyRef.current) return;
    if (!/^[0-9a-f]{64}$/.test(preview.review.review_digest)) return;
    if (preview.decision === 'continue' && (
      !preview.review.can_continue || !reconciliationDetailsMatch(preview.review, preview.execution)
    )) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await WorkflowApiService.decideReconciliation(
        preview.review.id, preview.decision, preview.review.review_digest,
      ).catch(() => WorkflowApiService.getReconciliationDecision(
        preview.review.id, preview.review.review_digest,
      ));
      if (result.id !== preview.review.id || result.execution_id !== preview.review.execution_id) {
        throw new Error('Reconciliation receipt scope mismatch');
      }
      if (!mounted.current) return;
      setPreview(null);
      setMessage(t(`${copy}.${result.decision === 'stop' ? 'stopped' : result.dispatch_status === 'unknown' ? 'dispatchUnknown' : 'continued'}`));
      setReload(value => value + 1);
    } catch {
      if (mounted.current) setError(t(`${copy}.decisionError`));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const previewContent = preview && <section ref={previewPanel} tabIndex={-1} role="dialog" aria-modal="false" aria-label={t(`${copy}.previewTitle`)} className="rounded-xl border border-purple-200 bg-white p-4 dark:border-purple-900 dark:bg-gray-900">
      <h5 className="font-semibold text-gray-900 dark:text-gray-100">{t(`${copy}.previewTitle`)}</h5>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm text-gray-700 dark:text-gray-200">{preview.review.summary}</p>
      <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">{t(`${copy}.${preview.decision === 'stop' ? 'stopScope' : 'continueScope'}`)}</p>
      <div className="mt-3 space-y-2">
        {preview.review.nodes.map(node => {
          const receipts = preview.execution && reconciliationDetailsMatch(preview.review, preview.execution)
            ? preview.execution.node_executions : [];
          const receipt = receipts.find(row => row.id === node.id);
          return <div key={node.id} className="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
            <p className="text-sm font-semibold text-gray-800 dark:text-gray-100">{node.label || t(`workflow.node_types.${node.node_type}.label`, { defaultValue: node.node_type })}</p>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.${node.status === 'completed' ? 'completedNode' : 'unknownNode'}`)}</p>
            {node.status === 'completed' && receipt && <div className="prose prose-sm mt-2 max-h-72 max-w-none overflow-auto break-words dark:prose-invert">
              <ReactMarkdown components={{ img: ({ alt }) => <span>{alt}</span>, a: ({ children }) => <span>{children}</span> }}>{reconciliationOutput(receipt.output)}</ReactMarkdown>
            </div>}
          </div>;
        })}
      </div>
      {!reconciliationDetailsMatch(preview.review, preview.execution) && <p role="alert" className="mt-3 text-sm text-amber-700 dark:text-amber-300">{t(`${copy}.detailsUnavailable`)}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={() => setPreview(null)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-700">{t(`${copy}.cancel`)}</button>
        <button type="button" disabled={busy || (preview.decision === 'continue' && (!preview.review.can_continue || !reconciliationDetailsMatch(preview.review, preview.execution)))} onClick={() => void decide()}
          className="rounded-lg bg-purple-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{t(`${copy}.${preview.decision === 'stop' ? 'confirmStop' : 'confirmContinue'}`)}</button>
      </div>
    </section>;

  return <section aria-label={t(`${copy}.title`)} className="space-y-3 rounded-2xl border border-amber-200 bg-amber-50/40 p-5 dark:border-amber-900/60 dark:bg-amber-950/10">
    <div className="flex items-start justify-between gap-3">
      <div>
        <h4 className="font-semibold text-gray-900 dark:text-gray-100">{t(`${copy}.title`)}</h4>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t(`${copy}.description`)}</p>
      </div>
      <button type="button" disabled={loading || busy} onClick={() => setReload(value => value + 1)}
        className="rounded-lg p-2 text-gray-500 hover:bg-amber-100 disabled:opacity-50 dark:hover:bg-amber-900/30" aria-label={t(`${copy}.refresh`)}>
        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
      </button>
    </div>
    {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p>}
    {message && <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">{message}</p>}
    {!loading && !error && items.length === 0 && <p className="text-sm text-gray-500 dark:text-gray-400">{t(`${copy}.empty`)}</p>}
    {items.map(item => <article key={item.id} className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <p className="whitespace-pre-wrap break-words text-sm font-semibold text-gray-900 dark:text-gray-100">{item.summary}</p>
      <p className="mt-2 break-all text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.execution`)} {item.execution_id}</p>
      <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">{item.can_continue ? t(`${copy}.savedResult`) : t(`${copy}.reasons.${item.blocked_reason ?? 'record_changed'}`)}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={() => void showPreview(item, 'stop')}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200">{t(`${copy}.inspectStop`)}</button>
        {item.can_continue && <button type="button" disabled={busy} onClick={() => void showPreview(item, 'continue')}
          className="rounded-lg bg-purple-600 px-3 py-2 text-sm font-semibold text-white hover:bg-purple-700 disabled:opacity-50">{t(`${copy}.inspectContinue`)}</button>}
      </div>
      {preview?.review.id === item.id && <div className="mt-4">{previewContent}</div>}
    </article>)}
    {hasMore && <button type="button" disabled={loading} onClick={() => void loadMore()} className="text-sm font-semibold text-purple-700 disabled:opacity-50 dark:text-purple-300">{t(`${copy}.loadMore`)}</button>}

  </section>;
}

export default function WorkflowReconciliations({ refreshToken = 0 }: { refreshToken?: number }) {
  const staff = useAuthStore(state => state.user?.id);
  const project = useAuthStore(state => state.user?.project_id);
  if (!staff || !project) return null;
  return <ReconciliationInbox key={`${project}:${staff}`} refreshToken={refreshToken} />;
}
