/** Access-token renewal and actual browser activity share one session policy. */
export function tokenNeedsRefresh(token: string | null, now = Date.now()): boolean {
  if (!token) return true;
  try {
    const payload: unknown = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (typeof payload !== 'object' || payload === null || !('exp' in payload)) return true;
    return typeof payload.exp !== 'number' || payload.exp * 1000 <= now + 60_000;
  } catch {
    return true;
  }
}

export function observeSessionActivity(
  renew: () => Promise<void>,
  target: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'> = document,
  now: () => number = Date.now,
): () => void {
  let lastAttempt = -Infinity;
  const onActivity = async (event: Event) => {
    if (!event.isTrusted || target.visibilityState !== 'visible') return;
    const timestamp = now();
    if (timestamp - lastAttempt < 60_000) return;
    lastAttempt = timestamp;
    try {
      await renew();
    } catch {
      // A temporary outage preserves the session and permits a later retry.
      lastAttempt = -Infinity;
    }
  };
  const events = ['pointerdown', 'keydown', 'visibilitychange'] as const;
  events.forEach(name => target.addEventListener(name, onActivity));
  return () => events.forEach(name => target.removeEventListener(name, onActivity));
}
