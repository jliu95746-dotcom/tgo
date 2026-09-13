import type { Message } from '@/types';

export interface AssistDraftContext {
  channelId: string | undefined;
  sourceId: string | null;
  text: string;
  enabled: boolean;
  skillName: string | null;
}

/** Preserve a media reference; never send a display label as the customer's question. */
export function getAssistCustomerInput(message: Message): {
  customerMessage: string; messageType: 1 | 2 | 4; mediaFileId?: string;
} | null {
  let kind = Number(message.payloadType ?? message.payload?.type ?? 1);
  const rawPayload = message.payload;
  if (kind === 3 && rawPayload && 'mime_type' in rawPayload
    && typeof rawPayload.mime_type === 'string' && rawPayload.mime_type.startsWith('audio/')) kind = 4;
  if (kind === 2 || kind === 4) {
    const payload = message.payload;
    const payloadUrl = payload && 'url' in payload && typeof payload.url === 'string' ? payload.url : '';
    const reference = payloadUrl || message.attachments?.[0]?.url || message.content.trim();
    if (!reference || !/^(?:https?:\/\/|\/(?:api\/)?v1\/chat\/files\/)/i.test(reference)) return null;
    const mediaFileId = payload && 'file_id' in payload ? payload.file_id : message.metadata?.file_id;
    return { customerMessage: reference, messageType: kind,
      ...(typeof mediaFileId === 'string' ? { mediaFileId } : {}) };
  }
  if (kind !== 1) return null;
  const customerMessage = message.content.trim();
  return customerMessage ? { customerMessage, messageType: 1 } : null;
}

export function getAssistSourceId(message: Message | undefined): string | null {
  if (!message) return null;
  return String(message.sourceMessageId || message.clientMsgNo || message.messageId
    || message.id || message.timestamp);
}

function compareMessages(left: Message, right: Message): number {
  if (left.messageSeq && right.messageSeq && left.messageSeq !== right.messageSeq) {
    return left.messageSeq - right.messageSeq;
  }
  const time = (Date.parse(left.timestamp) || 0) - (Date.parse(right.timestamp) || 0);
  if (time) return time;
  // When timestamps have only second precision, do not draft over a reply.
  return Number(left.type === 'staff') - Number(right.type === 'staff');
}

/** Select a customer turn only when no later outgoing reply/attempt already exists. */
export function getAssistDraftSource(messages: readonly Message[]): Message | undefined {
  const unique = new Map<string, Message>();
  for (const message of messages) {
    if (message.type === 'system') continue;
    const identity = message.clientMsgNo || message.messageId || message.id;
    const previous = unique.get(identity);
    // A server-acknowledged copy outranks an optimistic duplicate with a local clock.
    if (previous?.messageSeq && (!message.messageSeq || previous.messageSeq > message.messageSeq)) continue;
    unique.set(identity, message);
  }
  let latest: Message | undefined;
  for (const message of unique.values()) {
    if (!latest || compareMessages(message, latest) > 0) latest = message;
  }
  // Failed/unknown sends belong to the explicit retry flow, not a new auto-draft.
  return latest?.type === 'visitor' ? latest : undefined;
}

export function canApplyAssistDraft(request: AssistDraftContext, current: AssistDraftContext): boolean {
  return current.enabled && request.channelId === current.channelId
    && request.sourceId === current.sourceId && request.text === current.text
    && request.skillName === current.skillName;
}
