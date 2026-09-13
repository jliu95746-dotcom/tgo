import { useContext, useEffect } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import { wukongimWebSocketService } from '@/services/wukongimWebSocket';
import { notificationService, DEFAULT_NOTIFICATION_PREFERENCES } from '@/services/notificationService';
import { ToastContext } from '@/components/ui/ToastContainer';

/** One application-wide listener; read current account and preferences per event. */
export function QueueNotificationManager() {
  const token = useAuthStore(state => state.token);
  const userId = useAuthStore(state => state.user?.id);
  const projectId = useAuthStore(state => state.user?.project_id);
  const toast = useContext(ToastContext);
  const showToast = toast?.showToast;

  useEffect(() => {
    if (!token || !userId || !projectId) return;
    return wukongimWebSocketService.onQueueUpdated(event => {
      notificationService.checkAndNotifyQueue(
        event.raw,
        { ...DEFAULT_NOTIFICATION_PREFERENCES, ...useUIStore.getState().preferences },
        () => { window.location.href = '/chat?tab=unassigned'; },
        (title, body) => showToast?.('info', title, body, 8000),
      );
    });
  }, [token, userId, projectId, showToast]);

  return null;
}
