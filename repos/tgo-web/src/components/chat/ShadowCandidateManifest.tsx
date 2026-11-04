import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { messageAnalysisApiService } from '@/services/messageAnalysisApi';
import type {
  ShadowProtocolCandidateItem, ShadowProtocolCandidatePage, ShadowProtocolIntegrity,
} from '@/types/shadowReview';

/** Auditable UUID manifest; customer and reply text stay in the original chat. */
export default function ShadowCandidateManifest({ protocolId }: { protocolId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<ShadowProtocolCandidatePage | null>(null);
  const [items, setItems] = useState<ShadowProtocolCandidateItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [integrity, setIntegrity] = useState<ShadowProtocolIntegrity | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setFailed(false);
    setItems([]);
    setPage(null);
    setIntegrity(null);
    setCheckFailed(false);
    void messageAnalysisApiService.listShadowProtocolCandidates(
      protocolId, null, controller.signal,
    ).then(data => {
      if (!controller.signal.aborted) {
        setPage(data);
        setItems(data.items);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controllerRef.current?.abort();
  }, [open, protocolId]);

  async function nextPage() {
    if (!page?.next_cursor || loading) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setFailed(false);
    try {
      const next = await messageAnalysisApiService.listShadowProtocolCandidates(
        protocolId, page.next_cursor, controller.signal,
      );
      if (!controller.signal.aborted) {
        setPage(next);
        setItems(previous => [...previous, ...next.items]);
      }
    } catch {
      if (!controller.signal.aborted) setFailed(true);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function checkIntegrity() {
    if (checking) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setChecking(true);
    setCheckFailed(false);
    try {
      const result = await messageAnalysisApiService.verifyShadowProtocolIntegrity(
        protocolId, controller.signal,
      );
      if (!controller.signal.aborted) setIntegrity(result);
    } catch {
      if (!controller.signal.aborted) setCheckFailed(true);
    } finally {
      if (!controller.signal.aborted) setChecking(false);
    }
  }

  return <details className="mt-1" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-blue-600 dark:text-blue-400">
      {t('shadowReview.manifestTitle')}
    </summary>
    {loading && <p>{t('shadowReview.manifestLoading')}</p>}
    {failed && <p role="alert" className="text-red-600">
      {t('shadowReview.manifestFailed')}
    </p>}
    {page && <>
      <p>{t('shadowReview.manifestCount', { count: page.total_candidates })}</p>
      <p className="break-all">SHA-256: {page.population_sha256}</p>
      <button type="button" disabled={checking} onClick={() => void checkIntegrity()}
        className="mt-1 text-blue-600 hover:underline disabled:opacity-50 dark:text-blue-400">
        {checking ? t('shadowReview.manifestChecking') : t('shadowReview.manifestCheck')}
      </button>
      {checkFailed && <p role="alert" className="text-red-600">
        {t('shadowReview.manifestCheckFailed')}
      </p>}
      {integrity && <div role="status" className={integrity.consistent
        ? 'text-green-700 dark:text-green-400'
        : 'text-red-700 dark:text-red-400'}>
        <p>{integrity.consistent
          ? t('shadowReview.manifestConsistent')
          : t('shadowReview.manifestInconsistent')}</p>
        {!integrity.consistent && <ul className="list-inside list-disc">
          {!integrity.population_valid && <li>{t('shadowReview.manifestPopulationMismatch')}</li>}
          {!integrity.manifest_valid && <li>{t('shadowReview.manifestMappingMismatch')}</li>}
          {!integrity.sample_valid && <li>{t('shadowReview.manifestSampleMismatch')}</li>}
          {!integrity.metadata_valid && <li>{t('shadowReview.manifestMetadataMismatch')}</li>}
        </ul>}
        <p>{t('shadowReview.manifestCalculatedCount', { count: integrity.population_count })}</p>
        <p className="break-all">{t('shadowReview.manifestMappingHash')}: {integrity.manifest_sha256}</p>
      </div>}
      {items.length === 0 && <p>{t('shadowReview.manifestEmpty')}</p>}
      <ol className="mt-1 max-h-52 space-y-1 overflow-y-auto">
        {items.map(item => <li key={item.draft_id} className="break-all">
          <Link to={`/chat/251/${item.visitor_id}-vtr?shadowReview=1&shadowDraftId=${item.draft_id}`}
            className="text-blue-600 hover:underline dark:text-blue-400">
            {item.draft_id}
          </Link>
          <span className="ml-1">· {item.selected_position === null
            ? t('shadowReview.manifestNotSelected')
            : t('shadowReview.manifestSelected', { position: item.selected_position })}</span>
        </li>)}
      </ol>
      {page.next_cursor && <button type="button" disabled={loading}
        onClick={() => void nextPage()}
        className="mt-1 text-blue-600 hover:underline disabled:opacity-50 dark:text-blue-400">
        {t('shadowReview.manifestNext')}
      </button>}
    </>}
  </details>;
}
