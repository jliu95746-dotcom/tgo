import { ApiError, type TgoWidgetClient, type VisitorRequestProof } from './client.js';
import { ensureNoCustomerSessionEcho, type BusinessCustomerSessionInput } from './businessCustomerSession.js';

const FAILURE = '业务查询请求未完成，请重新验证或转人工。';
type JsonRecord = Record<string, unknown>;
const record = (value: unknown): JsonRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};

function eventPayload(data: unknown): JsonRecord {
  let value = record(data);
  for (let depth = 0; depth < 3 && value.data && !('content' in value || 'content_chunk' in value || 'final_content' in value || 'success' in value); depth++) {
    value = record(value.data);
  }
  return value;
}

/** All network calls use the existing client; private streams wait for verified completion. */
export async function sendBusinessChat(client: TgoWidgetClient, body: JsonRecord,
  sessions: readonly BusinessCustomerSessionInput[], assertIdentity: () => void, proof?: VisitorRequestProof): Promise<unknown> {
  const privateRequest = sessions.length > 0;
  const buffered = privateRequest || Boolean(proof);
  try {
    if (body.stream === false) {
      const result = proof ? await client.postVisitor('/v1/chat/completion', body, proof)
        : await client.postPublic('/v1/chat/completion', body);
      assertIdentity();
      ensureNoCustomerSessionEcho(result, sessions);
      return result;
    }
    let accumulated = '';
    let completed = false;
    let failed = false;
    let acknowledgement: JsonRecord | undefined;
    const stream = proof ? (callbacks: Parameters<TgoWidgetClient['stream']>[2]) => client.streamVisitor('/v1/chat/completion',body,callbacks,proof)
      : (callbacks: Parameters<TgoWidgetClient['stream']>[2]) => client.stream('/v1/chat/completion',body,callbacks);
    await stream({
      onMessage: (event, data) => {
        const envelope = record(data);
        const kind = typeof envelope.event_type === 'string' ? envelope.event_type : event;
        const payload = eventPayload(data);
        if (buffered) {
          try { ensureNoCustomerSessionEcho(data, sessions); assertIdentity(); }
          catch { failed = true; return; }
        }
        if (kind === 'team_run_content' || kind === 'agent_content_chunk') {
          const content = typeof payload.content_chunk === 'string' ? payload.content_chunk : typeof payload.content === 'string' ? payload.content : '';
          accumulated += content;
          if (!buffered && content) process.stdout.write(content);
          if (buffered && accumulated.length > 2 * 1024 * 1024) failed = true;
          if (proof && accumulated.includes(proof.imToken)) failed = true;
        } else if (kind === 'agent_response_complete' || kind === 'workflow_completed') {
          if (payload.success === true && typeof payload.final_content === 'string') {
            completed = true;
            if (buffered) accumulated = payload.final_content;
          } else if (buffered && !(kind === 'workflow_completed' && completed && payload.success !== false)) failed = true;
        } else if (['queued', 'human_handoff', 'ai_disabled', 'assist_mode', 'accepted'].includes(kind)) {
          if (privateRequest) failed = true;
          else acknowledgement = {event_type:kind};
        } else if (['error', 'workflow_failed', 'agent_execution_failed'].includes(kind)) {
          if (buffered) failed = true;
          else process.stderr.write(`\nStream error: ${payload.message || envelope.message || kind}\n`);
        }
      },
      onClose: () => { if (!buffered && accumulated) process.stdout.write('\n'); },
    });
    assertIdentity();
    if (buffered) {
      if (acknowledgement && !failed && !completed && !accumulated) {
        process.stdout.write(JSON.stringify(acknowledgement) + '\n');
        return acknowledgement;
      }
      if (!completed || failed) throw new Error(FAILURE);
      if (proof && accumulated.includes(proof.imToken)) throw new Error(FAILURE);
      ensureNoCustomerSessionEcho(accumulated, sessions);
      if (accumulated) process.stdout.write(accumulated + '\n');
    }
    return { content: accumulated, stream: true };
  } catch (error) {
    if (!privateRequest) throw error;
    if (error instanceof ApiError) throw new ApiError(error.status, { code: 'BUSINESS_REQUEST_FAILED', message: `${FAILURE} HTTP ${error.status}` });
    throw new Error(FAILURE);
  }
}
