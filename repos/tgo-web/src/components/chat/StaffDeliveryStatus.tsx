import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { deliveryKey, useStaffDeliveryStore } from '@/stores/staffDeliveryStore';

interface Props { staffId: string; clientMsgNo: string }

export default function StaffDeliveryStatus({ staffId, clientMsgNo }: Props) {
  const { t } = useTranslation();
  const entry = useStaffDeliveryStore(state => state.entries[deliveryKey(staffId, clientMsgNo)]);
  const [busy, setBusy] = useState(false);
  const status = entry?.receipt.delivery_status;
  const history = entry?.receipt.history_status;
  const training = entry?.receipt.training_status;
  const hasEntry = Boolean(entry);

  useEffect(() => {
    if (!hasEntry || status === 'failed' || status === 'unknown' || (history === 'sent' && training !== 'pending')) return;
    // Bounded visible polling, never a retry of the customer channel.
    let attempts = 0;
    const timer = window.setInterval(() => {
      if (++attempts > 12) { window.clearInterval(timer); return; }
      void useStaffDeliveryStore.getState().refresh(staffId, clientMsgNo);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [staffId, clientMsgNo, status, history, training, hasEntry]);

  if (!entry || (status === 'sent' && history === 'sent' && (!training || training === 'none'))) return null;
  const label = status === 'sent'
    ? history !== 'sent' ? t('chat.delivery.historyPending')
      : training === 'saved' ? t('chat.delivery.trainingSaved')
        : training === 'unavailable' ? t('chat.delivery.trainingUnavailable')
          : t('chat.delivery.trainingPending')
    : status === 'failed' ? t('chat.delivery.failed')
      : status === 'unknown' ? t('chat.delivery.unknown') : t('chat.delivery.processing');
  const act = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (status === 'failed') await useStaffDeliveryStore.getState().submit(staffId, entry.request);
      else await useStaffDeliveryStore.getState().refresh(staffId, clientMsgNo);
    } finally { setBusy(false); }
  };
  return (
    <div className={`mt-1 text-xs max-w-full ${status === 'failed' ? 'text-red-500' : 'text-amber-600 dark:text-amber-400'}`} role="status">
      <span>{label}</span>
      {entry.receipt.training_skill_name && training !== 'none' && <span> · {entry.receipt.training_skill_name}</span>}
      {entry.receipt.error_message && status === 'failed' && <span>：{entry.receipt.error_message}</span>}
      {training === 'unavailable' ? <a className="ml-2 underline" href="/ai/skills">{t('chat.delivery.viewSkills')}</a> : !(training === 'saved' && history === 'sent') && <button type="button" disabled={busy} onClick={() => void act()} className="ml-2 underline disabled:opacity-50">
        {status === 'failed' ? t('chat.delivery.retry') : t('chat.delivery.check')}
      </button>}
    </div>
  );
}
