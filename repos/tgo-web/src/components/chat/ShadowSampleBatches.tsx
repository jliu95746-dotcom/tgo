import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import { APIError } from '@/services/api';
import { useAuthStore } from '@/stores/authStore';
import type {
  ShadowReviewProtocol, ShadowReviewProtocolList,
  ShadowSampleBatch, ShadowSampleBatchList,
} from '@/types/shadowReview';
import ShadowCandidateManifest from './ShadowCandidateManifest';
import ShadowAdjudicationForm from './ShadowAdjudicationForm';
import ShadowAdjudicationHistory from './ShadowAdjudicationHistory';
import ShadowGateReadiness from './ShadowGateReadiness';

const REQUEST_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function readPendingRequestId(storageKey: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.sessionStorage.getItem(storageKey);
    return stored && REQUEST_ID_PATTERN.test(stored) ? stored : null;
  } catch { return null; }
}

/** Immutable membership is created by the API; this view never sends replies. */
export default function ShadowSampleBatches({ revision }: { revision: number }) {
  const { t } = useTranslation();
  const actor = useAuthStore(state => state.user);
  const requestStorageKey = `tgo:shadow-sample:${actor?.project_id ?? ''}:${actor?.id ?? ''}`;
  const [list, setList] = useState<ShadowSampleBatchList | null>(null);
  const [protocol, setProtocol] = useState<ShadowReviewProtocol | null>(null);
  const [protocolHistory, setProtocolHistory] = useState<ShadowReviewProtocolList | null>(null);
  const [protocolLoading, setProtocolLoading] = useState(true);
  const [protocolStart, setProtocolStart] = useState('');
  const [protocolDays, setProtocolDays] = useState(7);
  const [protocolSize, setProtocolSize] = useState(200);
  const [protocolBusy, setProtocolBusy] = useState(false);
  const [protocolFailed, setProtocolFailed] = useState(false);
  const [protocolHistoryFailed, setProtocolHistoryFailed] = useState(false);
  const [protocolActionFailed, setProtocolActionFailed] = useState(false);
  const [protocolValidationFailed, setProtocolValidationFailed] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [selected, setSelected] = useState<ShadowSampleBatch | null>(null);
  const [pendingRequestId, setPendingRequestId] = useState<string | null>(
    () => readPendingRequestId(requestStorageKey),
  );
  const [creating, setCreating] = useState(false);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [listFailed, setListFailed] = useState(false);
  const [actionFailed, setActionFailed] = useState(false);
  const [resultsLocked, setResultsLocked] = useState(false);
  const [listRevision, setListRevision] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  const detailRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    detailRequest.current?.abort();
    setSelected(null);
    setList(null);
    setProtocol(null);
    setProtocolHistory(null);
    setResultsLocked(false);
    setPendingRequestId(readPendingRequestId(requestStorageKey));
  }, [requestStorageKey]);

  useEffect(() => {
    const controller = new AbortController();
    setListFailed(false);
    setProtocolFailed(false);
    setProtocolHistoryFailed(false);
    setProtocolLoading(true);
    void messageAnalysisApiService.listShadowSampleBatches(controller.signal)
      .then(data => { if (!controller.signal.aborted) setList(data); })
      .catch(() => { if (!controller.signal.aborted) setListFailed(true); });
    void messageAnalysisApiService.getShadowReviewProtocol(controller.signal)
      .then(data => { if (!controller.signal.aborted) setProtocol(data); })
      .catch(() => { if (!controller.signal.aborted) setProtocolFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setProtocolLoading(false); });
    void messageAnalysisApiService.listShadowReviewProtocols(controller.signal)
      .then(data => { if (!controller.signal.aborted) setProtocolHistory(data); })
      .catch(() => { if (!controller.signal.aborted) setProtocolHistoryFailed(true); });
    return () => controller.abort();
  }, [revision, listRevision, actor?.id, actor?.project_id]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 60000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => () => detailRequest.current?.abort(), []);

  useEffect(() => {
    if (!selected?.id) return;
    const controller = new AbortController();
    void messageAnalysisApiService.getShadowSampleBatch(selected.id, controller.signal)
      .then(data => { if (!controller.signal.aborted) setSelected(data); })
      .catch(error => {
        if (controller.signal.aborted) return;
        setSelected(null);
        if (error instanceof APIError && error.getErrorCode() === 'SHADOW_BLIND_REVIEW_LOCKED') {
          setResultsLocked(true);
        } else {
          setActionFailed(true);
        }
      });
    return () => controller.abort();
  }, [revision, selected?.id]);

  async function createBatch() {
    if (creating) return;
    const requestId = pendingRequestId ?? crypto.randomUUID();
    setPendingRequestId(requestId);
    try { window.sessionStorage.setItem(requestStorageKey, requestId); }
    catch { /* The in-memory ID still protects retries in this open page. */ }
    setActionFailed(false);
    setResultsLocked(false);
    setCreating(true);
    try {
      const batch = await messageAnalysisApiService.createShadowSampleBatch({
        request_id: requestId,
        days: 7,
        sample_size: 200,
      });
      setSelected(batch);
      setPendingRequestId(null);
      try { window.sessionStorage.removeItem(requestStorageKey); }
      catch { /* A browser storage failure does not change the saved batch. */ }
      setListRevision(value => value + 1);
    } catch (error) {
      if (error instanceof APIError && error.getErrorCode() === 'SHADOW_BLIND_REVIEW_LOCKED') {
        setResultsLocked(true);
      } else {
        setActionFailed(true);
      }
    } finally {
      setCreating(false);
    }
  }

  async function registerProtocol() {
    if (protocolBusy || !protocolStart) {
      setProtocolValidationFailed(true);
      return;
    }
    const startMs = new Date(protocolStart).getTime();
    if (!Number.isFinite(startMs) || startMs < Date.now() + 60000
      || !Number.isInteger(protocolDays) || protocolDays < 1 || protocolDays > 90
      || !Number.isInteger(protocolSize) || protocolSize < 1 || protocolSize > 500) {
      setProtocolValidationFailed(true);
      return;
    }
    setProtocolBusy(true);
    setProtocolActionFailed(false);
    setProtocolValidationFailed(false);
    try {
      const saved = await messageAnalysisApiService.registerShadowReviewProtocol({
        request_id: crypto.randomUUID(),
        window_start: new Date(startMs).toISOString(),
        window_end: new Date(startMs + protocolDays * 86400000).toISOString(),
        sample_size: protocolSize,
        target_channel: 'wecom_kf',
      });
      setProtocol(saved);
      setListRevision(value => value + 1);
    } catch {
      setProtocolActionFailed(true);
      void messageAnalysisApiService.getShadowReviewProtocol()
        .then(setProtocol).catch(() => setProtocolFailed(true));
    } finally {
      setProtocolBusy(false);
    }
  }

  async function finalizeProtocol() {
    if (protocolBusy) return;
    setProtocolBusy(true);
    setProtocolActionFailed(false);
    try {
      const saved = await messageAnalysisApiService.finalizeShadowReviewProtocol();
      setProtocol(saved);
      if (saved.batch_id) await selectBatch(saved.batch_id);
      setListRevision(value => value + 1);
    } catch {
      setProtocolActionFailed(true);
      void messageAnalysisApiService.getShadowReviewProtocol()
        .then(setProtocol).catch(() => setProtocolFailed(true));
    } finally {
      setProtocolBusy(false);
    }
  }

  async function selectBatch(batchId: string) {
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setLoadingId(batchId);
    setActionFailed(false);
    setResultsLocked(false);
    try {
      const batch = await messageAnalysisApiService.getShadowSampleBatch(
        batchId, controller.signal,
      );
      if (!controller.signal.aborted) setSelected(batch);
    } catch (error) {
      if (!controller.signal.aborted) {
        setSelected(null);
        if (error instanceof APIError && error.getErrorCode() === 'SHADOW_BLIND_REVIEW_LOCKED') {
          setResultsLocked(true);
        } else {
          setActionFailed(true);
        }
      }
    } finally {
      if (!controller.signal.aborted) setLoadingId(null);
    }
  }

  return <details onToggle={event => setPanelOpen(event.currentTarget.open)}
    className="rounded-lg border border-gray-200 bg-white p-2 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
    <summary className="cursor-pointer font-medium">{t('shadowReview.sampleTitle')}</summary>
    <section className="mt-2 rounded border border-blue-200 p-2 dark:border-blue-900">
      <p className="font-medium">{t('shadowReview.protocolTitle')}</p>
      <p className="mt-1">{t('shadowReview.protocolIntro')}</p>
      {protocolLoading && <p>{t('shadowReview.protocolLoading')}</p>}
      {protocolFailed && <p role="alert" className="mt-1 text-red-600">
        {t('shadowReview.protocolLoadFailed')}
      </p>}
      {!protocolLoading && !protocolFailed && protocol?.status !== 'scheduled' && <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span>{t('shadowReview.protocolStart')}</span>
          <input type="datetime-local" value={protocolStart}
            onChange={event => setProtocolStart(event.target.value)}
            className="rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-600 dark:bg-gray-900" />
        </label>
        <label className="flex flex-col gap-1">
          <span>{t('shadowReview.protocolDays')}</span>
          <input type="number" min={1} max={90} value={protocolDays}
            onChange={event => setProtocolDays(Number(event.target.value))}
            className="w-20 rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-600 dark:bg-gray-900" />
        </label>
        <label className="flex flex-col gap-1">
          <span>{t('shadowReview.protocolSize')}</span>
          <input type="number" min={1} max={500} value={protocolSize}
            onChange={event => setProtocolSize(Number(event.target.value))}
            className="w-20 rounded border border-gray-300 bg-white px-2 py-1 dark:border-gray-600 dark:bg-gray-900" />
        </label>
        <button type="button" disabled={protocolBusy}
          onClick={() => void registerProtocol()}
          className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
          {t('shadowReview.protocolRegister')}
        </button>
      </div>}
      {protocol && <div className="mt-2 space-y-1">
        <p>{t(`shadowReview.protocolStatuses.${protocol.status}`)}</p>
        <p>{t(`shadowReview.protocolChannels.${protocol.target_channel}`)}</p>
        <p>{t('shadowReview.protocolWindow', {
          start: new Date(protocol.window_start).toLocaleString(),
          end: new Date(protocol.window_end).toLocaleString(),
          size: protocol.sample_size,
        })}</p>
        <p className="break-all">{t('shadowReview.protocolCommitment')}: {protocol.seed_sha256}</p>
        {protocol.status === 'scheduled' && <button type="button"
          disabled={protocolBusy || nowMs < new Date(protocol.window_end).getTime()}
          onClick={() => void finalizeProtocol()}
          className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
          {t('shadowReview.protocolFinalize')}
        </button>}
        {protocol.status === 'frozen' && protocol.batch_id && <button type="button"
          onClick={() => void selectBatch(protocol.batch_id!)}
          className="text-blue-600 hover:underline dark:text-blue-400">
          {t('shadowReview.protocolOpenBatch')}
        </button>}
        {protocol.population_count !== null && <p>{t('shadowReview.protocolPopulation', {
          count: protocol.population_count,
        })}</p>}
        {protocol.status !== 'scheduled' && <ShadowCandidateManifest protocolId={protocol.id} />}
        {panelOpen && <ShadowGateReadiness
          key={`${actor?.project_id ?? ''}:${actor?.id ?? ''}:${protocol.id}`}
          protocolId={protocol.id} revision={`${revision}:${listRevision}`}
          actorId={actor?.id ?? ''} />}
      </div>}
      {protocolActionFailed && <p role="alert" className="mt-1 text-red-600">
        {t('shadowReview.protocolActionFailed')}
      </p>}
      {protocolValidationFailed && <p role="alert" className="mt-1 text-red-600">
        {t('shadowReview.protocolValidationFailed')}
      </p>}
      <p className="mt-2 font-medium">{t('shadowReview.protocolHistory')}</p>
      {protocolHistoryFailed && <p role="alert" className="text-red-600">
        {t('shadowReview.protocolHistoryFailed')}
      </p>}
      {protocolHistory?.items.length === 0 && <p>{t('shadowReview.protocolHistoryEmpty')}</p>}
      <ul className="mt-1 space-y-1">
        {protocolHistory?.items.map(item => <li key={item.id}>
          {new Date(item.created_at).toLocaleString()} · {t(`shadowReview.protocolStatuses.${item.status}`)}
          {' · '}{t(`shadowReview.protocolChannels.${item.target_channel}`)}
          {item.batch_id && <button type="button"
            onClick={() => void selectBatch(item.batch_id!)}
            className="ml-1 text-blue-600 hover:underline dark:text-blue-400">
            {t('shadowReview.protocolOpenBatch')}
          </button>}
          {item.id !== protocol?.id && item.status !== 'scheduled'
            && <ShadowCandidateManifest protocolId={item.id} />}
        </li>)}
      </ul>
      {protocolHistory?.has_more && <p>{t('shadowReview.protocolHistoryLimit')}</p>}
    </section>
    <p className="mt-1">{t('shadowReview.sampleIntro')}</p>
    <button type="button" disabled={creating} onClick={() => void createBatch()}
      className="mt-2 rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50">
      {t(creating ? 'shadowReview.sampleCreating'
        : pendingRequestId ? 'shadowReview.sampleRetry' : 'shadowReview.sampleCreate')}
    </button>
    {actionFailed && <p role="alert" className="mt-1 text-red-600">
      {t('shadowReview.sampleActionFailed')}
    </p>}
    {resultsLocked && <p className="mt-1 text-blue-600 dark:text-blue-400">
      {t('shadowReview.blindResultsLocked')}
    </p>}
    <p className="mt-2 font-medium">{t('shadowReview.sampleHistory')}</p>
    {listFailed && <p role="alert" className="text-red-600">
      {t('shadowReview.sampleListFailed')}
    </p>}
    {list?.items.length === 0 && <p>{t('shadowReview.sampleNone')}</p>}
    <ul className="mt-1 space-y-1">
      {list?.items.map(batch => <li key={batch.id}>
        <button type="button" disabled={loadingId === batch.id}
          onClick={() => void selectBatch(batch.id)}
          className="text-left text-blue-600 hover:underline disabled:opacity-50 dark:text-blue-400">
          {t('shadowReview.sampleHistoryItem', {
            time: new Date(batch.created_at).toLocaleString(),
            count: batch.sample_count,
            total: batch.population_count,
          })}
        </button>
      </li>)}
    </ul>
    {list?.has_more && <p>{t('shadowReview.sampleHistoryLimit')}</p>}
    {selected && <div className="mt-2 border-t border-gray-200 pt-2 dark:border-gray-700">
      <p className="font-medium">{t('shadowReview.sampleSelected', {
        count: selected.sample_count, total: selected.population_count,
      })}</p>
      <p>{t('shadowReview.sampleWindow', {
        start: new Date(selected.window_start).toLocaleString(),
        end: new Date(selected.window_end).toLocaleString(),
      })}</p>
      <p>{t('shadowReview.sampleProgress', {
        reviewed: selected.reviewed_count,
        unreviewed: selected.unreviewed_count,
        insufficient: selected.insufficient_count,
      })}</p>
      <p>{t('shadowReview.sampleCrossReview', {
        ready: selected.ready_count,
        resolved: selected.resolved_count,
        second: selected.needs_second_count,
        disputed: selected.disputed_count,
        insufficient: selected.insufficient_case_count,
        severe: selected.severe_flagged_count,
      })}</p>
      <p>{t('shadowReview.sampleSevere', {
        ever: selected.ever_severe_count,
        current: selected.current_severe_count,
        missing: selected.missing_drafts_count,
      })}</p>
      {selected.sample_count < selected.requested_size && <p>
        {t('shadowReview.sampleShortfall')}
      </p>}
      <details className="mt-1">
        <summary className="cursor-pointer">{t('shadowReview.sampleProof')}</summary>
        <p className="break-all">{t('shadowReview.sampleAlgorithm')}: {selected.algorithm}</p>
        <p className="break-all">{t('shadowReview.sampleSeed')}: {selected.random_seed}</p>
        <p className="break-all">{t('shadowReview.samplePopulationHash')}: {selected.population_sha256}</p>
      </details>
      <ol className="mt-1 max-h-80 space-y-1 overflow-y-auto">
        {selected.items.map(item => <li key={item.draft_id}>
          <Link className="text-blue-600 hover:underline dark:text-blue-400"
            to={`/chat/251/${item.visitor_id}-vtr?shadowReview=1&shadowDraftId=${item.draft_id}`}>
            {t('shadowReview.sampleCase', { position: item.position })}
          </Link>
          <span className="ml-1">· {t(`shadowReview.sampleStatuses.${item.review_status}`)}</span>
          <span className="ml-1 text-gray-500 dark:text-gray-400">· {t('shadowReview.sampleReviewerCount', { count: item.reviewer_count })}</span>
          {item.adjudication_verdict && <span className="ml-1 text-blue-600 dark:text-blue-400">
            · {t('shadowReview.adjudicationDecision', { verdict: t(`shadowReview.verdicts.${item.adjudication_verdict}`) })}
          </span>}
          {item.review_set_sha256 && !item.adjudication_verdict &&
            <ShadowAdjudicationForm batchId={selected.id} item={item}
              onSaved={() => {
                setListRevision(value => value + 1);
                void selectBatch(selected.id);
              }} />}
          {item.reviewer_count >= 2 && <ShadowAdjudicationHistory
            key={`${item.draft_id}-${item.review_set_sha256}-${item.adjudication_verdict}`}
            batchId={selected.id} item={item} />}
        </li>)}
      </ol>
      <p className="mt-1">{t('shadowReview.sampleCaveat')}</p>
    </div>}
  </details>;
}
