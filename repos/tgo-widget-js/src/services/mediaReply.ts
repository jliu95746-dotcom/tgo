import type { ChatFileUploadResponse } from './upload'

export interface MediaReplyRequest {
  apiBase: string
  apiKey: string
  fromUid: string
  channelId: string
  channelType: number
  sourceMessageId: string
  upload: ChatFileUploadResponse
  deliveryConfirmed: boolean
}

/** A confirmed attachment remains sent even when starting its AI reply fails. */
export async function requestMediaReply(request: MediaReplyRequest): Promise<void> {
  if (!request.deliveryConfirmed) return
  const mime = request.upload.file_type.toLowerCase()
  const msgType = mime.startsWith('image/') ? 2 : mime.startsWith('audio/') ? 4 : null
  if (msgType === null) return
  if (!request.apiKey || !request.fromUid) throw new Error('Missing media reply context')
  const response = await fetch(`${request.apiBase.replace(/\/$/, '')}/v1/chat/completion`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      api_key: request.apiKey, from_uid: request.fromUid, channel_id: request.channelId,
      channel_type: request.channelType, source_message_id: request.sourceMessageId,
      message: request.upload.file_url, media_file_id: request.upload.file_id, msg_type: msgType,
      forward_user_message_to_wukongim: false, wukongim_only: true, stream: false,
    }),
    signal: AbortSignal.timeout(20000),
  })
  if (!response.ok) throw new Error(`Media reply request failed (HTTP ${response.status})`)
  const body: unknown = await response.json()
  if (!body || typeof body !== 'object' || !('event_type' in body)
    || !['accepted', 'assist_mode', 'ai_disabled', 'queued', 'human_handoff'].includes(String(body.event_type))) {
    throw new Error('Invalid media reply acknowledgement')
  }
}
