import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { billingApi } from '../../services/billingApi';
import { useAuthStore } from '../../stores/authStore';

type TrialStatus = 'checking' | 'pending' | 'ready';

export default function TrialActivationButton({ onStatus }: { onStatus: (status: TrialStatus) => void }) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let current = true;
    void billingApi.subscription()
      .then(subscription => {
        if (!current) return;
        const isPending = subscription.status === 'pending';
        setPending(isPending);
        onStatus(isPending ? 'pending' : 'ready');
      })
      .catch(() => { if (current) onStatus('ready'); });
    return () => { current = false; };
  }, [user?.id, onStatus]);

  async function activate(event: FormEvent) {
    event.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true); setError('');
    try {
      await billingApi.redeemTrialCode(code.trim());
      setCode('');
      window.location.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('trialActivation.failed'));
      setBusy(false);
    }
  }

  if (!pending || user?.role !== 'admin') return null;
  return <>
    <button type="button" onClick={() => setOpen(true)}
      className="fixed right-4 top-4 z-40 rounded-full bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-lg hover:bg-indigo-700">
      {t('trialActivation.button')}
    </button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <section role="dialog" aria-modal="true" aria-labelledby="trial-activation-title"
        className="w-full max-w-sm rounded-xl bg-white p-6 text-slate-900 shadow-xl">
        <h2 id="trial-activation-title" className="text-lg font-semibold">{t('trialActivation.title')}</h2>
        <p className="mt-2 text-sm text-slate-600">{t('trialActivation.hint')}</p>
        {error && <p role="alert" className="mt-4 text-sm text-red-700">{error}</p>}
        <form onSubmit={activate} className="mt-5 space-y-4">
          <label className="block text-sm">{t('trialActivation.code')}
            <input required maxLength={64} autoComplete="off" value={code}
              onChange={event => setCode(event.target.value)}
              className="mt-2 w-full rounded-lg border border-slate-300 p-2" />
          </label>
          <div className="flex justify-end gap-3">
            <button type="button" disabled={busy} onClick={() => { setOpen(false); setError(''); }}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm">{t('trialActivation.cancel')}</button>
            <button type="submit" disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">
              {t('trialActivation.activate')}
            </button>
          </div>
        </form>
      </section>
    </div>}
  </>;
}
