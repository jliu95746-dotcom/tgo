import { getJSON, setJSON } from '../utils/storage'
import { loadCachedVisitor, removeCachedVisitor } from './visitor'

type PendingVisitorEnd = Readonly<{ visitor_id: string; visitor_end_token: string }>
export type VisitorLifecycleOptions = Readonly<{ apiBase: string; platformApiKey: string }>
export type VisitorEndOutcome = Readonly<{
  accessEnded: boolean
  upstreamStopStatus: 'confirmed' | 'pending'
  imDisconnectStatus: 'requested' | 'pending'
}>

const pendingOutcome: VisitorEndOutcome = { accessEnded: false, upstreamStopStatus: 'pending', imDisconnectStatus: 'pending' }
const key = (options: VisitorLifecycleOptions) => `tgo:visitor-ends:${options.apiBase}:${options.platformApiKey}`

function pending(options: VisitorLifecycleOptions): PendingVisitorEnd[] {
  const value = getJSON<unknown>(key(options))
  if (!Array.isArray(value)) return []
  return value.slice(0, 20).filter((item): item is PendingVisitorEnd => !!item && typeof item === 'object'
    && Object.keys(item).length === 2 && typeof item.visitor_id === 'string' && !!item.visitor_id
    && typeof item.visitor_end_token === 'string' && !!item.visitor_end_token && item.visitor_end_token.length <= 8192)
}

async function submit(options: VisitorLifecycleOptions, identity: PendingVisitorEnd): Promise<VisitorEndOutcome> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 8000)
  try {
    const response = await fetch(`${options.apiBase.replace(/\/$/, '')}/v1/visitors/access/end`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ platform_api_key: options.platformApiKey, ...identity }), signal: controller.signal,
    })
    if (!response.ok) return pendingOutcome
    const data: unknown = await response.json()
    if (!data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).length !== 3) return pendingOutcome
    const receipt = data as Record<string, unknown>
    if (receipt.status !== 'ended' || !['confirmed', 'pending'].includes(String(receipt.upstream_stop_status))
      || !['requested', 'pending'].includes(String(receipt.im_disconnect_status))) return pendingOutcome
    const result: VisitorEndOutcome = {
      accessEnded: true, upstreamStopStatus: receipt.upstream_stop_status as 'confirmed' | 'pending',
      imDisconnectStatus: receipt.im_disconnect_status as 'requested' | 'pending',
    }
    if (result.upstreamStopStatus === 'confirmed' && result.imDisconnectStatus === 'requested') {
      setJSON(key(options), pending(options).filter(item => item.visitor_id !== identity.visitor_id))
    }
    return result
  } catch {
    return pendingOutcome
  } finally { clearTimeout(timer) }
}

export async function endCachedVisitorAccess(options: VisitorLifecycleOptions): Promise<VisitorEndOutcome> {
  const cached = loadCachedVisitor(options.apiBase, options.platformApiKey)
  if (!cached) return pendingOutcome
  // Delete all readable access before awaiting anything or storing retry metadata.
  removeCachedVisitor(options.apiBase, options.platformApiKey)
  if (loadCachedVisitor(options.apiBase, options.platformApiKey) !== null) throw new Error('旧会话访问缓存无法清理，请先清理浏览器存储。')
  if (typeof cached.visitor_end_token !== 'string' || !cached.visitor_end_token || cached.visitor_end_token.length > 8192) return pendingOutcome
  return endIssuedVisitorAccess(options, {id:cached.visitor_id,visitor_end_token:cached.visitor_end_token})
}

export async function endIssuedVisitorAccess(options: VisitorLifecycleOptions, issued: {
  id: string; visitor_end_token?: string
}): Promise<VisitorEndOutcome> {
  if (typeof issued.visitor_end_token !== 'string' || !issued.visitor_end_token || issued.visitor_end_token.length>8192) return pendingOutcome
  const identity: PendingVisitorEnd = {visitor_id:issued.id,visitor_end_token:issued.visitor_end_token}
  const previous = pending(options).filter(item => item.visitor_id !== identity.visitor_id)
  if (previous.length >= 20) throw new Error('旧会话退出尚待确认，请先重试退出。')
  setJSON(key(options), [...previous, identity])
  return submit(options, identity)
}

export async function retryPendingVisitorEnds(options: VisitorLifecycleOptions): Promise<readonly VisitorEndOutcome[]> {
  const results: VisitorEndOutcome[] = []
  for (const identity of pending(options)) results.push(await submit(options, identity))
  return results
}
