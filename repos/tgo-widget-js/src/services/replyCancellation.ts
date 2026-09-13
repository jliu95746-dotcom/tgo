/** A queued request or an HTTP success without confirmation is not a stop. */
export async function requestReplyCancellation(params: {
  apiBase: string;
  platformApiKey: string;
  clientMsgNo: string;
  reason?: string;
}): Promise<void> {
  const response = await fetch(`${params.apiBase.replace(/\/$/, '')}/v1/ai/runs/cancel-by-client`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      platform_api_key: params.platformApiKey,
      client_msg_no: params.clientMsgNo,
      reason: params.reason || 'user_cancel',
    }),
    signal: AbortSignal.timeout(12000),
  });
  const data: unknown = await response.json();
  if (
    !response.ok || !data || typeof data !== 'object' ||
    !('accepted' in data) || data.accepted !== true ||
    !('status' in data) || data.status !== 'cancelled' ||
    !('client_msg_no' in data) || data.client_msg_no !== params.clientMsgNo
  ) {
    throw new Error('Reply cancellation not confirmed');
  }
}
