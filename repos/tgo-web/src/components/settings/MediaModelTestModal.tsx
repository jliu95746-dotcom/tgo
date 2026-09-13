import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { MediaProbeResult, MediaProbeTarget } from '@/types/mediaProbe';
import { probeMediaModel } from '@/services/mediaProbeApi';
import { mediaProbeAccept, mediaProbeFileError } from '@/utils/mediaProbe';

export default function MediaModelTestModal({ target, onClose }: { target: MediaProbeTarget; onClose: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<MediaProbeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const pending = useRef(false);
  const active = useRef(true);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    active.current = true;
    closeButton.current?.focus();
    return () => { active.current = false; };
  }, []);
  const run = async () => {
    if (pending.current || !file || !confirmed) return;
    const error = mediaProbeFileError(file, target.capability);
    if (error) { setResult({ success: false, message: t(`mediaProbe.${error}`) }); return; }
    pending.current = true; setBusy(true); setResult(null);
    try {
      const response = await probeMediaModel(target, file);
      if (active.current) setResult(response);
    } catch (error: unknown) {
      if (active.current) setResult({ success: false, message: error instanceof Error ? error.message : t('mediaProbe.failed') });
    } finally {
      pending.current = false;
      if (active.current) setBusy(false);
    }
  };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onKeyDown={event => { if (event.key === 'Escape' && !busy) onClose(); }}>
    <section role="dialog" aria-modal="true" aria-labelledby="media-model-test-title" className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-xl bg-white p-6 dark:bg-gray-900">
      <h2 id="media-model-test-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">{t('mediaProbe.title')} · {t(`modelSetup.uses.${target.capability}`)}</h2>
      <p className="mt-2 break-all text-sm text-gray-500">{target.providerName} / {target.modelId}</p>
      <div className="mt-4 space-y-4 overflow-y-auto text-sm text-gray-700 dark:text-gray-200">
        <p>{t('mediaProbe.hint')}</p>
        <label className="block">{t(target.capability === 'asr' ? 'mediaProbe.audio' : 'mediaProbe.image')}
          <input type="file" disabled={busy} accept={mediaProbeAccept[target.capability]} className="mt-2 block w-full rounded-lg border border-gray-300 p-3 dark:border-gray-700"
            onChange={event => {
              const selected = event.target.files?.[0] || null;
              const error = selected ? mediaProbeFileError(selected, target.capability) : null;
              setFile(error ? null : selected); setConfirmed(false);
              setResult(error ? { success: false, message: t(`mediaProbe.${error}`) } : null);
            }} />
        </label>
        <p className="text-xs text-gray-500">{t(target.capability === 'asr' ? 'mediaProbe.audioFormats' : 'mediaProbe.imageFormats')}</p>
        <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} disabled={busy || !file} onChange={e => setConfirmed(e.target.checked)} />{t('mediaProbe.consent', { provider: target.providerName })}</label>
        {result && <div role="status" className="rounded-lg bg-gray-100 p-3 dark:bg-gray-800">
          <p className={result.success ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>{t(result.success ? 'modelSetup.testSuccess' : 'modelSetup.testFailed')}</p>
          <p className="mt-2">{result.message}</p>
          {result.output && <pre className="mt-3 max-h-60 overflow-auto whitespace-pre-wrap break-words font-sans">{result.output}</pre>}
        </div>}
      </div>
      <div className="mt-5 flex justify-end gap-3">
        <button ref={closeButton} disabled={busy} onClick={onClose} className="rounded-lg border border-gray-300 px-4 py-2 text-sm dark:border-gray-600 dark:text-gray-200 disabled:opacity-40">{t('common.close')}</button>
        <button onClick={run} disabled={busy || !file || !confirmed} className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-40">{t(busy ? 'modelSetup.testing' : 'mediaProbe.run')}</button>
      </div>
    </section>
  </div>;
}
