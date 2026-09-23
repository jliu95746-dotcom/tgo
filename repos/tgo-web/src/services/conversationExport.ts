import type { WuKongIMMessage, WuKongIMMessageSyncResponse } from '@/types';

interface ExportOptions {
  maxMessages?: number;
  maxBytes?: number;
  cancelled?: () => boolean;
}

export async function collectConversationExport(
  channelId: string,
  channelType: number,
  loadPage: (beforeSeq: number) => Promise<WuKongIMMessageSyncResponse>,
  options: ExportOptions = {},
): Promise<WuKongIMMessage[]> {
  const messages = new Map<number, WuKongIMMessage>();
  let cursor = 0;
  let bytes = 0;
  const checkCancellation = () => {
    if (options.cancelled?.()) throw new Error('EXPORT_CANCELLED');
  };
  for (let page = 0; page < 201; page++) {
    checkCancellation();
    const response = await loadPage(cursor);
    checkCancellation();
    let minimum = Infinity;
    for (const message of response.messages) {
      if (message.channel_id !== channelId || message.channel_type !== channelType
        || !Number.isSafeInteger(message.message_seq) || message.message_seq < 1) {
        throw new Error('EXPORT_INVALID');
      }
      minimum = Math.min(minimum, message.message_seq);
      if (!messages.has(message.message_seq)) {
        bytes += new TextEncoder().encode(JSON.stringify(message)).length;
        messages.set(message.message_seq, message);
      }
      if (messages.size > (options.maxMessages ?? 10000) || bytes > (options.maxBytes ?? 25 * 1024 * 1024)) {
        throw new Error('EXPORT_LIMIT');
      }
    }
    if (!response.more) return [...messages.values()].sort((left, right) => left.message_seq - right.message_seq);
    const next = minimum - 1;
    if (!Number.isSafeInteger(next) || next < 1 || (cursor !== 0 && next >= cursor)) {
      throw new Error('EXPORT_PAGINATION');
    }
    cursor = next;
  }
  throw new Error('EXPORT_LIMIT');
}
