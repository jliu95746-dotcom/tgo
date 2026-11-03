import type { BusinessCustomerSessionInput } from '../types/businessCustomer'
import { ensureNoCustomerSessionEcho } from './businessCustomerSession'
import { captureVisitorDataAccess } from './visitorDataAccess'

export type VisitorTextCompletion = {
  apiBase: string
  platformApiKey: string
  message: string
  fromUid: string
  channelId?: string
  channelType?: number
  sourceMessageId: string
  customerSessions?: readonly BusinessCustomerSessionInput[]
}

export async function sendTextCompletion(params: VisitorTextCompletion): Promise<unknown> {
  const access = captureVisitorDataAccess(params)
  const body = {
    api_key: params.platformApiKey, message: params.message, from_uid: params.fromUid,
    wukongim_only: true, forward_user_message_to_wukongim: false, stream: false,
    source_message_id: params.sourceMessageId, channel_id: params.channelId, channel_type: params.channelType,
    ...(params.customerSessions?.length ? { customer_sessions: params.customerSessions } : {}),
  }
  const response = await fetch(`${params.apiBase.replace(/\/$/, '')}/v1/chat/completion`, {
    method: 'POST', headers: { ...access.headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  access.assertCurrent()
  let value: unknown
  try { value = await response.json() }
  catch { throw new Error(`/v1/chat/completion failed: HTTP ${response.status}`) }
  access.assertCurrent()
  access.assertSafeValue(value)
  ensureNoCustomerSessionEcho(value, params.customerSessions)
  const data = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  if (!response.ok || data.event_type === 'error') {
    const detail = typeof data.message === 'string' ? data.message : typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`
    throw new Error(`/v1/chat/completion failed: ${detail}`)
  }
  return value
}
