import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useNotification } from '@/hooks/useNotification';

/** An in-flow notice, never an automatic browser permission prompt. */
export default function NotificationPermissionNotice() {
  const { t } = useTranslation();
  const { permission, isSupported, preferences } = useNotification();
  const [dismissed, setDismissed] = useState(false);
  if (dismissed || !preferences.notificationEnabled || (isSupported && permission === 'granted')) return null;

  const reason = !isSupported ? 'unsupported' : permission === 'denied' ? 'denied' : 'default';
  return (
    <aside className="shrink-0 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100" aria-label={t('settings.notifications.notice.title')}>
      <p className="flex-1 min-w-48">{t(`settings.notifications.notice.${reason}`)}</p>
      <Link to="/settings/notifications" className="font-medium underline underline-offset-2">
        {t('settings.notifications.notice.settings')}
      </Link>
      <button type="button" onClick={() => setDismissed(true)} className="rounded px-2 py-1 hover:bg-amber-100 dark:hover:bg-amber-900">
        {t('settings.notifications.notice.dismiss')}
      </button>
    </aside>
  );
}
