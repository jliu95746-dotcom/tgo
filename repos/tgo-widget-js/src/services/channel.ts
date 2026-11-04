import { captureVisitorDataAccess } from './visitorDataAccess'

export type ChannelInfo = {
  name?: string
  avatar?: string
  channel_id?: string
  channel_type?: number
  entity_type?: 'visitor' | 'staff' | string
}

export type StaffInfo = { name?: string; avatar?: string }

export async function fetchChannelInfo(params: {
  apiBase: string
  platformApiKey: string
  channelId: string
  channelType: number
  signal?: AbortSignal
}): Promise<ChannelInfo> {
  const { apiBase, platformApiKey, channelId, channelType, signal } = params
  const access = captureVisitorDataAccess({apiBase,platformApiKey})
  const base = apiBase.replace(/\/$/, '')
  const url = `${base}/v1/channels/info` +
    `?channel_id=${encodeURIComponent(channelId)}` +
    `&channel_type=${encodeURIComponent(String(channelType))}`

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: { ...access.headers, 'X-Platform-API-Key': platformApiKey },
      signal: signal ?? controller.signal,
    })
    access.assertCurrent()
    if (!res.ok) throw new Error(`[Channel] info failed: HTTP ${res.status}`)
    const value: unknown = await res.json()
    access.assertCurrent()
    access.assertSafeValue(value)
    if (!value || typeof value !== 'object') throw new Error('[Channel] 无效的显示信息')
    const data = value as Record<string,unknown>
    if (data.channel_id !== channelId || data.channel_type !== channelType ||
        typeof data.name !== 'string' || typeof data.avatar !== 'string') throw new Error('[Channel] 显示信息不匹配')
    return {name:data.name,avatar:data.avatar,channel_id:channelId,channel_type:channelType}
  } finally {
    clearTimeout(timeout)
  }
}
