import { getJSON, setJSON, remove } from '../utils/storage'
import type { VisitorRegisterRequest, VisitorRegisterResponse, CachedVisitor } from '../types/api'
import { acquireBusinessCustomerSessions, captureBusinessCustomerSessionState, CustomerIdentityChangedError, ensureNoCustomerSessionEcho } from './businessCustomerSession'

const VISITOR_CACHE_KEY = (apiBase: string, platformApiKey: string) => `tgo:visitor:${apiBase}:${platformApiKey}`

export function loadCachedVisitor(apiBase: string, platformApiKey: string): CachedVisitor | null {
  return getJSON<CachedVisitor>(VISITOR_CACHE_KEY(apiBase, platformApiKey))
}

export function removeCachedVisitor(apiBase: string, platformApiKey: string): void {
  remove(VISITOR_CACHE_KEY(apiBase, platformApiKey))
}

class RegistrationAccessError extends Error {
  constructor(readonly status: number) { super(`[Visitor] register failed: HTTP ${status}`) }
}

export function saveCachedVisitor(apiBase: string, platformApiKey: string, v: VisitorRegisterResponse, expiresAtMs?: number) {
  const cached: CachedVisitor = {
    apiBase,
    platform_api_key: platformApiKey,
    visitor_id: v.id,
    platform_open_id: v.platform_open_id,
    channel_id: v.channel_id,
    channel_type: v.channel_type,
    im_token: v.im_token,
    visitor_access_token: v.visitor_access_token,
    visitor_end_token: v.visitor_end_token,
    business_protected: v.business_protected,
    business_access_expires_at: v.business_access_expires_at,
    project_id: v.project_id,
    platform_id: v.platform_id,
    created_at: v.created_at,
    updated_at: v.updated_at,
    expires_at: expiresAtMs,
  }
  setJSON(VISITOR_CACHE_KEY(apiBase, platformApiKey), cached)
}

export async function registerVisitor(params: {
  apiBase: string
  platformApiKey: string
  signal?: AbortSignal
  extra?: Omit<VisitorRegisterRequest, 'platform_api_key'>
}): Promise<VisitorRegisterResponse> {
  const { apiBase, platformApiKey, signal, extra } = params
  const url = `${apiBase.replace(/\/$/, '')}/v1/visitors/register`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platform_api_key: platformApiKey,
        ...extra,
      } satisfies VisitorRegisterRequest),
      signal: signal ?? controller.signal,
    })
    if (!res.ok) {
      throw new RegistrationAccessError(res.status)
    }
    const data = (await res.json()) as VisitorRegisterResponse
    ensureNoCustomerSessionEcho(JSON.stringify(data), extra?.customer_sessions ?? [])
    if (!data?.id || !data?.channel_id) {
      throw new Error('[Visitor] invalid register response: missing id/channel_id')
    }
    if (!data?.im_token) {
      throw new Error('[Visitor] Missing im_token in refreshed session')
    }
    if (typeof data.visitor_access_token !== 'string' || !data.visitor_access_token || data.visitor_access_token.length > 256) {
      throw new Error('[Visitor] Missing visitor access credential; update the server before connecting')
    }
    if (data.visitor_end_token !== undefined && (typeof data.visitor_end_token !== 'string'
      || !data.visitor_end_token || data.visitor_end_token.length > 8192)) throw new Error('退出凭据无效，请刷新重试。')
    return data
  } finally {
    clearTimeout(timeout)
  }
}

// Refresh credentials on every new IM session; cache identifies the visitor only.
export async function refreshVisitorSession(params: {
  apiBase: string
  platformApiKey: string
  extra?: Omit<VisitorRegisterRequest, 'platform_api_key' | 'platform_open_id' | 'visitor_id' | 'visitor_access_token' | 'im_token' | 'customer_sessions'>
  assertCurrent?: () => void
}): Promise<VisitorRegisterResponse> {
  params.assertCurrent?.()
  const state = await captureBusinessCustomerSessionState()
  const assertCurrent = (issued?: VisitorRegisterResponse) => {
    try { params.assertCurrent?.(); state.assertCurrent() }
    catch (error) {
      if (issued?.visitor_end_token) void import('./visitorLifecycle').then(lifecycle =>
        lifecycle.endIssuedVisitorAccess(params,issued)).catch(() => {})
      throw error
    }
  }
  assertCurrent()
  let cached = loadCachedVisitor(params.apiBase, params.platformApiKey)
  if (cached && state.hasProvider !== !!cached.business_protected) {
    const lifecycle = await import('./visitorLifecycle')
    assertCurrent()
    await lifecycle.endCachedVisitorAccess(params)
    assertCurrent()
    cached = null
  }
  if (cached && !cached.platform_open_id) {
    throw new Error('[Visitor] Missing cached visitor identity')
  }
  let lease: Awaited<ReturnType<typeof acquireBusinessCustomerSessions>> | undefined
  try {
    if (cached?.visitor_id && cached.project_id && cached.platform_id) {
      lease = await acquireBusinessCustomerSessions({ visitorId: cached.visitor_id,
        projectId: cached.project_id, platformId: cached.platform_id }, {
        apiBase: params.apiBase, visitorAccessToken: cached.visitor_access_token || '',
      })
      lease.assertCurrent()
    }
    if (cached?.business_protected && !lease?.sessions?.length) throw new Error('[Visitor] Current customer verification required')
  } catch (error) {
    assertCurrent()
    if (cached && error instanceof CustomerIdentityChangedError) {
      const lifecycle = await import('./visitorLifecycle')
      assertCurrent()
      await lifecycle.endCachedVisitorAccess(params)
      assertCurrent()
      cached = null; lease = undefined
    } else {
      removeCachedVisitor(params.apiBase, params.platformApiKey)
      throw error
    }
  }
  let visitor: VisitorRegisterResponse
  let startedFresh = !cached
  try {
    visitor = await registerVisitor({
    ...params,
    extra: {
      ...params.extra,
      ...(cached ? { visitor_id: cached.visitor_id, platform_open_id: cached.platform_open_id, visitor_access_token: cached.visitor_access_token,
        im_token: cached.visitor_access_token ? undefined : cached.im_token, customer_sessions: lease?.sessions } : {}),
    },
    })
  } catch (error) {
    if (!(error instanceof RegistrationAccessError) || error.status !== 401 || !cached) throw error
    // Lost/expired ordinary possession cannot restore a public ID. Start a distinct guest.
    removeCachedVisitor(params.apiBase, params.platformApiKey)
    visitor = await registerVisitor({ apiBase: params.apiBase, platformApiKey: params.platformApiKey, extra: params.extra })
    startedFresh = true
  }
  assertCurrent(visitor)
  lease?.assertCurrent()
  if (startedFresh && visitor.id && visitor.project_id && visitor.platform_id) {
    const initial = visitor
    lease = await acquireBusinessCustomerSessions({visitorId: initial.id, projectId: initial.project_id, platformId: initial.platform_id}, {
      apiBase: params.apiBase, visitorAccessToken: initial.visitor_access_token,
    })
    assertCurrent(initial)
    lease.assertCurrent()
    if (lease.sessions?.length) {
      visitor = await registerVisitor({apiBase:params.apiBase,platformApiKey:params.platformApiKey,
        extra:{visitor_id:initial.id,platform_open_id:initial.platform_open_id,visitor_access_token:initial.visitor_access_token,
          customer_sessions:lease.sessions}})
      assertCurrent(visitor)
      lease.assertCurrent()
    }
  }
  assertCurrent(visitor)
  if (!visitor.im_token) throw new Error('[Visitor] Missing im_token in refreshed session')
  saveCachedVisitor(params.apiBase, params.platformApiKey, visitor)
  return visitor
}

