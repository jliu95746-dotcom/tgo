import { useEffect, useState } from 'react';
import { useAuthStore } from '../stores/authStore';
import { apiClient } from '../services/api';
import { avatarSessionDeadline, privateVisitorAvatarPath, readVisitorAvatar } from '../services/visitorAvatar';

/** Revalidate private bytes; release old images on logout, switch and failure. */
export function useVisitorAvatar(url: string): string {
  const token = useAuthStore(state => state.token);
  const actor = useAuthStore(state => state.user?.id ?? '');
  const authenticated = useAuthStore(state => state.isAuthenticated);
  const key = JSON.stringify([actor, token, url, authenticated]);
  const path = privateVisitorAvatarPath(url);
  const deadline = avatarSessionDeadline(token);
  const [image, setImage] = useState<{ key: string; url: string } | null>(null);
  useEffect(() => {
    if (path === null || !path || !authenticated || !token || !actor || deadline <= Date.now()) return;
    let active = true;
    let objectUrl = '';
    let request: AbortController | null = null;
    let timeout: number | undefined;
    const clear = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = '';
      if (active) setImage(null);
    };
    const refresh = async () => {
      request?.abort();
      window.clearTimeout(timeout);
      clear();
      if (document.hidden || deadline <= Date.now()) return;
      const current = new AbortController();
      request = current;
      timeout = window.setTimeout(() => current.abort(), 10_000);
      try {
        const blob = await readVisitorAvatar(url, token, current.signal);
        if (!active || current.signal.aborted || request !== current || apiClient.getToken() !== token || deadline <= Date.now()) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({ key, url: objectUrl });
      } catch {
        if (request === current) clear();
      } finally {
        if (request === current) window.clearTimeout(timeout);
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 30_000);
    const expiryTimer = window.setTimeout(() => { request?.abort(); clear(); }, Math.min(deadline - Date.now(), 2_147_483_647));
    const visible = () => { if (document.hidden) { request?.abort(); clear(); } else void refresh(); };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('focus', visible);
    return () => {
      active = false; request?.abort(); clear();
      window.clearTimeout(timeout); window.clearInterval(timer);
      window.clearTimeout(expiryTimer);
      document.removeEventListener('visibilitychange', visible);
      window.removeEventListener('focus', visible);
    };
  }, [key, path, actor, token, authenticated, url, deadline]);
  if (path === null) return url;
  return authenticated && actor && token && deadline > Date.now() && image?.key === key ? image.url : '';
}
