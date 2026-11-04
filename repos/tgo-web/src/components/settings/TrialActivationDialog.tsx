import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { billingApi } from '../../services/billingApi';

export default function TrialActivationDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { dialog.current?.showModal(); }, []);

  async function activate(event: FormEvent) {
    event.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true);
    setError('');
    try {
      await billingApi.redeemTrialCode(code.trim());
      window.location.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('trialActivation.failed'));
      setBusy(false);
    }
  }

  return <dialog ref={dialog} aria-labelledby={`${id}-title`} aria-describedby={`${id}-hint`}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    className="m-auto w-[min(92vw,24rem)] max-h-[90vh] overflow-auto rounded-xl bg-white p-6 text-slate-900 shadow-xl backdrop:bg-black/50 dark:bg-slate-800 dark:text-slate-100">
    <h2 id={`${id}-title`} className="text-lg font-semibold">{t('trialActivation.title')}</h2>
    <p id={`${id}-hint`} className="mt-2 text-sm text-slate-600 dark:text-slate-300">{t('trialActivation.hint')}</p>
    {error && <p role="alert" className="mt-4 text-sm text-red-700 dark:text-red-400">{error}</p>}
    <form onSubmit={activate} className="mt-5 space-y-4">
      <label className="block text-sm">{t('trialActivation.code')}
        <input required maxLength={64} autoComplete="off" value={code} disabled={busy}
          onChange={event => setCode(event.target.value)}
          className="mt-2 w-full rounded-lg border border-slate-300 bg-white p-2 dark:border-slate-600 dark:bg-slate-900" />
      </label>
      <div className="flex justify-end gap-3">
        <button type="button" disabled={busy} onClick={onClose}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-50 dark:border-slate-600">{t('trialActivation.cancel')}</button>
        <button type="submit" disabled={busy || !code.trim()} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50">
          {t('trialActivation.activate')}
        </button>
      </div>
    </form>
  </dialog>;
}
