import { useEffect } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { observeSessionActivity, tokenNeedsRefresh } from '@/services/staffSession';
import { STORAGE_KEYS } from '@/constants';

/** Restore the cookie session before opening protected pages or chat sockets. */
export function useStaffSession(): boolean {
  const authenticated = useAuthStore(state => state.isAuthenticated);
  const ready = useAuthStore(state => state.isSessionReady);

  useEffect(() => {
    const state = useAuthStore.getState();
    if (!authenticated) {
      state.setSessionReady();
      return;
    }
    void state.refreshSession(document.visibilityState === 'visible')
      .catch(error => console.warn('Unable to restore browser session:', error))
      .finally(() => useAuthStore.getState().setSessionReady());
    const stopActivity = observeSessionActivity(() => useAuthStore.getState().refreshSession(true));
    const onStorage = (event: StorageEvent) => {
      if ((event.key === STORAGE_KEYS.AUTH_TOKEN || event.key === STORAGE_KEYS.AUTH) && event.newValue === null) {
        const current = useAuthStore.getState();
        if (current.isAuthenticated) void current.logout(false);
      }
    };
    window.addEventListener('storage', onStorage);
    // Keep short access tokens usable for downloads/streams too. This never
    // touches the server's idle deadline, including when the tab is hidden.
    const timer = window.setInterval(() => {
      const current = useAuthStore.getState();
      if (current.isAuthenticated && tokenNeedsRefresh(current.token)) {
        void current.refreshSession(false).catch(error => {
          console.warn('Unable to renew browser access token:', error);
        });
      }
    }, 30_000);
    return () => {
      stopActivity(); window.clearInterval(timer);
      window.removeEventListener('storage', onStorage);
    };
  }, [authenticated]);

  return ready;
}
