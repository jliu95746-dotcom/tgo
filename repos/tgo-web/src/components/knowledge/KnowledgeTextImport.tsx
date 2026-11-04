import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { createKnowledgeTextFile } from '@/utils/knowledgeOnboarding';

interface TextDraft { title: string; body: string }
interface FieldsProps { body: string; onChange: (body: string) => void; disabled?: boolean }

export function KnowledgeBodyField({ body, onChange, disabled }: FieldsProps) {
  const { t } = useTranslation();
  return <div className="space-y-2">
    <label htmlFor="knowledge-body" className="block text-sm font-medium">{t('knowledgeOnboarding.body')}</label>
    <textarea id="knowledge-body" value={body} onChange={(event) => onChange(event.target.value)} disabled={disabled} rows={8}
      placeholder={t('knowledgeOnboarding.bodyPlaceholder')}
      className="w-full min-w-0 rounded-lg border border-gray-300 bg-white p-3 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100" />
    <p className="text-xs text-gray-500 dark:text-gray-400">{t('knowledgeOnboarding.bodyHint', { count: body.length })}</p>
  </div>;
}

export function KnowledgeTextImport({ initialDraft, onImport, onClose }: {
  initialDraft: TextDraft;
  onImport: (file: File) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [draft, setDraft] = useState(initialDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return <div role="dialog" aria-modal="true" aria-labelledby="text-import-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3">
    <form className="max-h-[90dvh] w-full max-w-2xl space-y-4 overflow-y-auto rounded-xl bg-white p-5 text-gray-900 shadow-xl dark:bg-gray-800 dark:text-gray-100" onSubmit={async (event) => {
      event.preventDefault();
      if (busy || !draft.title.trim() || !draft.body.trim()) return;
      setBusy(true); setError(null);
      try { await onImport(createKnowledgeTextFile(draft.title, draft.body)); if (mounted.current) onClose(); }
      catch (cause) { if (mounted.current) setError(`${cause instanceof Error ? cause.message : t('knowledgeOnboarding.importFailed')} ${t('knowledgeOnboarding.retryHint')}`); }
      finally { if (mounted.current) setBusy(false); }
    }}>
      <h3 id="text-import-title" className="text-lg font-semibold">{t('knowledgeOnboarding.paste')}</h3>
      <p className="text-sm text-gray-500">{t('knowledgeOnboarding.importHint')}</p>
      <label className="block text-sm">{t('knowledgeOnboarding.documentTitle')}
        <input autoFocus required maxLength={100} disabled={busy} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} className="mt-2 w-full rounded-lg border border-gray-300 bg-transparent p-3 dark:border-gray-600" />
      </label>
      <KnowledgeBodyField body={draft.body} onChange={(body) => setDraft({ ...draft, body })} disabled={busy} />
      {error && <p role="alert" className="break-words text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="flex flex-wrap justify-end gap-3">
        <button type="button" disabled={busy} onClick={onClose} className="rounded-lg border px-4 py-2 disabled:opacity-50">{t('common.cancel')}</button>
        <button type="submit" disabled={busy || !draft.title.trim() || !draft.body.trim()} className="rounded-lg bg-blue-600 px-4 py-2 text-white disabled:opacity-50">{t(busy ? 'knowledgeOnboarding.importing' : 'knowledgeOnboarding.import')}</button>
      </div>
    </form>
  </div>;
}
