const CHANNEL_ID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;

function requestEntry(endpoint: string, method: 'GET' | 'POST', signal?: AbortSignal) {
  const timeout = AbortSignal.timeout(1800);
  const options: RequestInit & { targetAddressSpace: 'loopback' } = {
    method,
    targetAddressSpace: 'loopback',
    credentials: 'omit',
    redirect: 'error',
    cache: 'no-store',
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  };
  return fetch(endpoint, options);
}

export async function findLocalReceptionEntry(channelId: string, signal: AbortSignal): Promise<string | null> {
  if (!CHANNEL_ID.test(channelId) || !window.isSecureContext) return null;
  const seed = Number.parseInt(channelId.slice(-6), 16);
  for (const offset of [0, 3571, 7142]) {
    if (signal.aborted) return null;
    const port = 38000 + (seed + offset) % 10000;
    const endpoint = `http://127.0.0.1:${port}/dashboard/reception/${channelId.toLowerCase()}`;
    try {
      const response = await requestEntry(endpoint, 'GET', signal);
      if (response.ok && (await response.json())?.ready === true) return endpoint;
      // A recognized bridge reports that this channel is still getting ready.
      if (response.status === 503) return null;
    } catch {
      // The bridge may be offline, or the browser may deny loopback access.
    }
  }
  return null;
}

export async function openLocalReceptionEntry(endpoint: string): Promise<boolean> {
  try {
    const response = await requestEntry(endpoint, 'POST');
    if (!response.ok) return false;
    const payload: { url?: unknown } = await response.json();
    if (typeof payload.url !== 'string') return false;
    const url = new URL(payload.url);
    if (
      url.origin !== new URL(endpoint).origin || url.username || url.password ||
      url.pathname !== '/' || url.hash || !/^\?local=[A-Za-z0-9_-]{43}$/.test(url.search)
    ) return false;
    window.location.assign(url.href);
    return true;
  } catch {
    return false;
  }
}
