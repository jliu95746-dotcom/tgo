import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { hiddenShadowApi } from '@/services/hiddenShadowApi';
import { APIError } from '@/services/api';
import { useAuthStore } from '@/stores/authStore';
import type {
  HiddenShadowCasePage, HiddenShadowIntegrity,
  HiddenShadowProtocol, HiddenShadowProtocolList, HiddenShadowReviewSummary,
} from '@/types/hiddenShadow';
import HiddenShadowCaseReview from './HiddenShadowCaseReview';
import HiddenShadowGate from './HiddenShadowGate';

function requestKey(projectId: string, actorId: string): string {
  return `tgo:hidden-shadow-protocol:${projectId}:${actorId}`;
}

function readRequestId(key: string): string | null {
  try { return sessionStorage.getItem(key); }
  catch { return null; }
}

function displayDate(value: string): string {
  return new Date(value).toLocaleString();
}

/** Admin registration and evidence inspection; no customer delivery path. */
export default function HiddenShadowProtocols({
  revision, initialOpen = false,
}: { revision: number; initialOpen?: boolean }) {
  const { t } = useTranslation();
  const actor = useAuthStore(state => state.user);
  const key = requestKey(actor?.project_id ?? '', actor?.id ?? '');
  const [open, setOpen] = useState(initialOpen);
  const [list, setList] = useState<HiddenShadowProtocolList | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [start, setStart] = useState('');
  const [days, setDays] = useState(7);
  const [size, setSize] = useState(200);
  const [selected, setSelected] = useState<HiddenShadowProtocol | null>(null);
  const [page, setPage] = useState<HiddenShadowCasePage | null>(null);
  const [integrity, setIntegrity] = useState<HiddenShadowIntegrity | null>(null);
  const [summary, setSummary] = useState<HiddenShadowReviewSummary | null>(null);
  const [summaryFailed, setSummaryFailed] = useState(false);
  const [reviewCandidateId, setReviewCandidateId] = useState<string | null>(null);
  const [inspectFailed, setInspectFailed] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const pendingId = useRef<string | null>(null);
  const activeProtocolId = useRef<string | null>(null);

  useEffect(() => { pendingId.current = readRequestId(key); }, [key]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoadFailed(false);
    void hiddenShadowApi.list(controller.signal)
      .then(data => { if (!controller.signal.aborted) setList(data); })
      .catch(() => { if (!controller.signal.aborted) setLoadFailed(true); });
    return () => controller.abort();
  }, [open, revision, actor?.id, actor?.project_id]);

  async function refresh(): Promise<void> {
    setLoadFailed(false);
    try { setList(await hiddenShadowApi.list()); }
    catch { setLoadFailed(true); }
  }

  function updateForm(update: () => void): void {
    update();
    pendingId.current = null;
    try { sessionStorage.removeItem(key); } catch { /* Storage is optional. */ }
    setActionError(null);
  }

  async function register(): Promise<void> {
    const startMs = new Date(start).getTime();
    if (!Number.isFinite(startMs) || startMs < Date.now() + 60000
      || !Number.isInteger(days) || days < 1 || days > 90
      || !Number.isInteger(size) || size < 1 || size > 500) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setBusy(true);
    const requestId = pendingId.current ?? crypto.randomUUID();
    pendingId.current = requestId;
    try { sessionStorage.setItem(key, requestId); } catch { /* Keep in memory. */ }
    try {
      const saved = await hiddenShadowApi.register({
        request_id: requestId,
        window_start: new Date(startMs).toISOString(),
        window_end: new Date(startMs + days * 86400000).toISOString(),
        sample_size: size,
      });
      pendingId.current = null;
      try { sessionStorage.removeItem(key); } catch { /* No persistent state needed. */ }
      setActionError(null);
      setSelected(saved);
      await refresh();
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.retry'));
      await refresh();
    } finally { setBusy(false); }
  }

  async function finalize(protocol: HiddenShadowProtocol): Promise<void> {
    setBusy(true);
    setActionError(null);
    try {
      const saved = await hiddenShadowApi.finalize(protocol.id);
      setSelected(saved);
      setPage(null);
      setIntegrity(null);
      await refresh();
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.finalizeFailed'));
      await refresh();
    } finally { setBusy(false); }
  }

  async function cancel(protocol: HiddenShadowProtocol): Promise<void> {
    if (!window.confirm(t('hiddenShadow.cancelConfirm'))) return;
    setBusy(true);
    setActionError(null);
    try {
      const saved = await hiddenShadowApi.cancel(protocol.id);
      setSelected(saved);
      setPage(null);
      setIntegrity(null);
      await refresh();
    } catch (error) {
      setActionError(error instanceof APIError
        ? `${error.getUserMessage()} (${error.getErrorCode()})`
        : t('hiddenShadow.cancelFailed'));
      await refresh();
    } finally { setBusy(false); }
  }

  async function inspect(protocol: HiddenShadowProtocol): Promise<void> {
    activeProtocolId.current = protocol.id;
    setSelected(protocol);
    setPage(null);
    setIntegrity(null);
    setSummary(null);
    setReviewCandidateId(null);
    setInspectFailed(false);
    setInspecting(true);
    try {
      const [nextPage, check] = await Promise.all([
        hiddenShadowApi.cases(protocol.id, null),
        hiddenShadowApi.integrity(protocol.id),
      ]);
      setPage(nextPage);
      setIntegrity(check);
      void refreshReviewSummary(protocol.id);
    } catch { setInspectFailed(true); }
    finally { setInspecting(false); }
  }

  async function refreshReviewSummary(protocolId: string): Promise<void> {
    setSummaryFailed(false);
    try {
      const result = await hiddenShadowApi.reviewSummary(protocolId);
      if (activeProtocolId.current === protocolId) setSummary(result);
    } catch {
      if (activeProtocolId.current === protocolId) setSummaryFailed(true);
    }
  }

  async function loadMore(): Promise<void> {
    if (!selected || !page?.next_cursor) return;
    setInspecting(true);
    setInspectFailed(false);
    try {
      const next = await hiddenShadowApi.cases(selected.id, page.next_cursor);
      setPage({ ...next, items: [...page.items, ...next.items] });
    } catch { setInspectFailed(true); }
    finally { setInspecting(false); }
  }

  return <section className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-gray-700 dark:border-blue-900 dark:bg-gray-800 dark:text-gray-200">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}
      className="font-semibold text-blue-700 dark:text-blue-300">{t('hiddenShadow.title')}</button>
    {open && <div className="mt-3 space-y-3">
      <p>{t('hiddenShadow.intro')}</p>
      <div className="flex flex-wrap items-end gap-2">
        <label>{t('hiddenShadow.start')}
          <input type="datetime-local" value={start} onChange={event => updateForm(() => setStart(event.target.value))}
            className="block rounded border border-gray-300 bg-white p-1 text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-white" />
        </label>
        <label>{t('hiddenShadow.days')}
          <input type="number" min={1} max={90} value={days} onChange={event => updateForm(() => setDays(Number(event.target.value)))}
            className="block w-20 rounded border border-gray-300 bg-white p-1 text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-white" />
        </label>
        <label>{t('hiddenShadow.size')}
          <input type="number" min={1} max={500} value={size} onChange={event => updateForm(() => setSize(Number(event.target.value)))}
            className="block w-20 rounded border border-gray-300 bg-white p-1 text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-white" />
        </label>
        <button type="button" disabled={busy} onClick={() => void register()}
          className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
          {t(busy ? 'hiddenShadow.registering' : 'hiddenShadow.register')}
        </button>
      </div>
      {invalid && <p role="alert" className="text-red-600">{t('hiddenShadow.invalid')}</p>}
      {actionError && <p role="alert" className="text-red-600">{actionError}</p>}
      <div className="flex items-center justify-between">
        <strong>{t('hiddenShadow.history')}</strong>
        <button type="button" onClick={() => void refresh()} className="text-blue-600 dark:text-blue-400">{t('shadowReview.refresh')}</button>
      </div>
      {loadFailed && <p role="alert" className="text-red-600">{t('hiddenShadow.loadFailed')}</p>}
      {list?.items.length === 0 && <p>{t('hiddenShadow.none')}</p>}
      {list?.items.map(protocol => <article key={protocol.id} className="rounded border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <p className="font-medium">{t(`hiddenShadow.status.${protocol.status}`)}</p>
        <p>{t('hiddenShadow.window', { start: displayDate(protocol.window_start), end: displayDate(protocol.window_end), size: protocol.sample_size })}</p>
        <p className="break-all text-gray-500">{t('hiddenShadow.commitment')}:
          {' '}{protocol.seed_sha256 ?? t('hiddenShadow.commitmentMissing')}
        </p>
        {protocol.manifest_sha256 && <p className="break-all text-gray-500">{t('hiddenShadow.manifest')}: {protocol.manifest_sha256}</p>}
        {protocol.population_count !== null && <p>{t('hiddenShadow.counts', { total: protocol.population_count, sample: protocol.sample_count })}</p>}
        {protocol.status === 'scheduled' && protocol.seed_sha256 && Date.now() >= new Date(protocol.window_end).getTime() &&
          <button type="button" disabled={busy} onClick={() => void finalize(protocol)} className="mt-1 mr-3 text-blue-600 disabled:opacity-50 dark:text-blue-400">
            {t(busy ? 'hiddenShadow.finalizing' : 'hiddenShadow.finalize')}
          </button>}
        {protocol.status === 'scheduled' && <button type="button" disabled={busy} onClick={() => void cancel(protocol)}
          className="mt-1 mr-3 text-red-600 disabled:opacity-50 dark:text-red-400">
          {t(busy ? 'hiddenShadow.cancelling' : 'hiddenShadow.cancel')}
        </button>}
        {protocol.cancelled_at && <p className="text-gray-500">
          {t('hiddenShadow.cancelledAt', { date: displayDate(protocol.cancelled_at) })}
        </p>}
        {protocol.status === 'frozen' && <button type="button" disabled={inspecting} onClick={() => void inspect(protocol)}
          className="mt-1 text-blue-600 disabled:opacity-50 dark:text-blue-400">{t('hiddenShadow.inspect')}</button>}
      </article>)}
      {list?.has_more && <p>{t('hiddenShadow.more')}</p>}
      {selected?.status === 'frozen' && <div className="rounded border border-gray-200 p-2 dark:border-gray-700">
        {inspecting && <p>{t('hiddenShadow.inspecting')}</p>}
        {inspectFailed && <p role="alert" className="text-red-600">{t('hiddenShadow.integrityFailed')}</p>}
        {integrity && <p className={integrity.consistent ? 'text-green-700 dark:text-green-300' : 'text-red-600'}>
          {t(integrity.consistent ? 'hiddenShadow.consistent' : 'hiddenShadow.inconsistent')}
        </p>}
        {summary && <p>{t('hiddenShadow.reviewProgress', {
          completed: summary.completed_cases, total: summary.selected_cases,
        })}</p>}
        {summaryFailed && <p role="alert" className="text-red-600">{t('hiddenShadow.summaryFailed')}</p>}
        {page?.items.map(item => <div key={item.candidate_id} className="break-all border-t border-gray-200 py-1 dark:border-gray-700">
          <button type="button" disabled={!integrity?.consistent} onClick={() => setReviewCandidateId(item.candidate_id)}
            className="text-left text-blue-600 disabled:text-gray-500 dark:text-blue-400 dark:disabled:text-gray-500">
            {t('hiddenShadow.case', { position: item.selected_position, id: item.candidate_id })}
          </button>
        </div>)}
        {selected && reviewCandidateId && integrity?.consistent && <HiddenShadowCaseReview
          key={`${selected.id}:${reviewCandidateId}`}
          protocolId={selected.id} candidateId={reviewCandidateId}
          reviewComplete={summary?.review_complete ?? false}
          onSaved={() => { void refreshReviewSummary(selected.id); }} />}
        {page && page.items.length === 0 && <p>{t('hiddenShadow.noCases')}</p>}
        {page?.next_cursor && <button type="button" disabled={inspecting} onClick={() => void loadMore()}
          className="text-blue-600 disabled:opacity-50 dark:text-blue-400">{t('hiddenShadow.next')}</button>}
        <p className="mt-2 text-gray-500">{t('hiddenShadow.reviewPending')}</p>
        {selected && <HiddenShadowGate key={selected.id} protocolId={selected.id} actorId={actor?.id ?? ''} />}
      </div>}
    </div>}
  </section>;
}
