import type { Message } from '@/types';

export interface AIOriginVerification {
  projectId: string | null;
  verified: Set<string>;
}

export const originKey = (channelId: string, clientNo: string): string =>
  JSON.stringify([channelId, clientNo]);

export function isVerifiedAIReply(
  message: Pick<Message, 'type' | 'senderKind' | 'channelId' | 'channelType' | 'clientMsgNo'>,
  projectId: string | null,
  proof: AIOriginVerification,
): boolean {
  return message.type === 'staff'
    && message.senderKind === 'ai'
    && message.channelType === 251
    && Boolean(message.channelId && message.clientMsgNo)
    && proof.projectId === projectId
    && proof.verified.has(originKey(message.channelId!, message.clientMsgNo!));
}
