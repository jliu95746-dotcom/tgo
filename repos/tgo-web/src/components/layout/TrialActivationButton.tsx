import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { billingApi } from '../../services/billingApi';
import { useAuthStore } from '../../stores/authStore';
import TrialActivationDialog from '../settings/TrialActivationDialog';

type TrialStatus = 'checking' | 'pending' | 'ready';

export default function TrialActivationButton({ onStatus }: { onStatus: (status: TrialStatus) => void }) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState(false);

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

  if (!pending || user?.role !== 'admin') return null;
  return <>
    <aside aria-label={t('trialActivation.noticeTitle')}
      className="shrink-0 flex items-center justify-between gap-4 border-b border-indigo-200 bg-indigo-50 px-4 py-2 text-sm text-indigo-900 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-100">
      <p className="min-w-0">{t('trialActivation.noticeTitle')}</p>
      <button type="button" onClick={() => setOpen(true)}
        className="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">
        {t('trialActivation.button')}
      </button>
    </aside>
    {open && <TrialActivationDialog onClose={() => setOpen(false)} />}
  </>;
}
