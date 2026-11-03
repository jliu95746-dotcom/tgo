import { captureVisitorDataAccess } from './visitorDataAccess'

export type AIReplyOriginEvidence = {
  client_msg_no: string
  status: 'verified' | 'unrecorded' | 'missing' | 'mismatch' | 'unavailable'
}

export async function verifyAIReplyOrigins(params: {
  apiBase: string
  platformApiKey: string
  channelId: string
  clientMsgNos: string[]
  signal?: AbortSignal
}): Promise<AIReplyOriginEvidence[]> {
  const access = captureVisitorDataAccess(params)
  const response = await fetch(`${params.apiBase.replace(/\/$/, '')}/v1/ai/runs/reply-origins/verify-by-visitor`, {
    method: 'POST',
    headers: { ...access.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      platform_api_key: params.platformApiKey,
      channel_id: params.channelId,
      client_msg_nos: params.clientMsgNos,
    }),
    signal: params.signal,
  })
  access.assertCurrent()
  if (!response.ok) throw new Error(`AI origin verification failed: HTTP ${response.status}`)
  const evidence: unknown = await response.json()
  access.assertCurrent()
  access.assertSafeValue(evidence)
  if (!Array.isArray(evidence)) throw new Error('Invalid AI origin verification response')
  return evidence as AIReplyOriginEvidence[]
}
