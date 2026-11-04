import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { deliveryKey, useStaffDeliveryStore } from '@/stores/staffDeliveryStore';
import { shouldPollStaffDelivery, staffDeliveryLabel } from '@/utils/staffDeliveryStatus';

interface Props { staffId: string; clientMsgNo: string }
const STATUS_POLL_INTERVAL_MS = 15_000;
const MAX_STATUS_POLL_ATTEMPTS = 24;

export default function StaffDeliveryStatus({ staffId, clientMsgNo }: Props) {
  const { t } = useTranslation();
  const entry = useStaffDeliveryStore(state => state.entries[deliveryKey(staffId, clientMsgNo)]);
  const [busy, setBusy] = useState(false);
  const status = entry?.receipt.delivery_status;
  const history = entry?.receipt.history_status;
  const training = entry?.receipt.training_status;
  const transport = entry?.receipt.delivery_transport;
  const accessDenied = entry?.accessDenied;
  const shouldPoll = Boolean(entry && shouldPollStaffDelivery(entry.receipt, accessDenied));

  useEffect(() => {
    if (!shouldPoll) return;
    // An offline bridge may return later. These are read-only receipt checks;
    // never submit again, overlap requests, or poll a hidden/unmounted workbench.
    let attempts = 0;
    let checking = false;
    const timer = window.setInterval(() => {
      if (checking || document.visibilityState === 'hidden') return;
      if (transport !== 'wechat_local' && ++attempts > MAX_STATUS_POLL_ATTEMPTS) { window.clearInterval(timer); return; }
      checking = true;
      void useStaffDeliveryStore.getState().refresh(staffId, clientMsgNo)
        .finally(() => { checking = false; });
    }, STATUS_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [staffId, clientMsgNo, status, history, training, transport, shouldPoll]);

  const labelKey = entry && staffDeliveryLabel(entry.receipt, accessDenied);
  if (!entry || !labelKey) return null;
  const label = t(labelKey);
  const complete = status === 'sent' && history === 'sent' && (!training || training === 'none' || training === 'saved');
  const retryable = status === 'failed' && transport !== 'wechat_local';
  const act = async () => {
    if (busy || accessDenied) return;
    setBusy(true);
    try {
      if (retryable) await useStaffDeliveryStore.getState().submit(staffId, entry.request);
      else await useStaffDeliveryStore.getState().refresh(staffId, clientMsgNo);
    } finally { setBusy(false); }
  };
  return (
    <div className={`mt-1 text-xs max-w-full ${status === 'failed' ? 'text-red-500' : complete ? 'text-gray-500 dark:text-gray-400' : 'text-amber-600 dark:text-amber-400'}`} role="status">
      {transport === 'wechat_local' && status === 'sent' && labelKey !== 'chat.delivery.wechatSent' && labelKey !== 'chat.delivery.wechatHistoryPending' && <span>{t('chat.delivery.wechatSent')} · </span>}
      <span>{label}</span>
      {entry.receipt.training_skill_name && training !== 'none' && <span> · {entry.receipt.training_skill_name}</span>}
      {entry.receipt.error_message && status === 'failed' && <span>：{entry.receipt.error_message}</span>}
      {!accessDenied && (training === 'unavailable' ? <a className="ml-2 underline" href="/ai/skills">{t('chat.delivery.viewSkills')}</a> : !complete && <button type="button" disabled={busy} onClick={() => void act()} className="ml-2 underline disabled:opacity-50">
        {retryable ? t('chat.delivery.retry') : t('chat.delivery.check')}
      </button>)}
    </div>
  );
}
