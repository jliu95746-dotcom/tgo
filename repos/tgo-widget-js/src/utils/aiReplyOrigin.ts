import type { ChatMessage } from '../types/chat'

export const originKey = (channelId: string, clientMsgNo: string): string =>
  JSON.stringify([channelId, clientMsgNo])

export function isVerifiedAIReply(
  message: Pick<ChatMessage, 'role' | 'senderKind' | 'channelId' | 'channelType' | 'clientMsgNo'>,
  currentChannelId: string | undefined,
  verified: Set<string>,
): boolean {
  return message.role === 'agent'
    && message.senderKind === 'ai'
    && message.channelType === 251
    && Boolean(message.channelId && message.clientMsgNo)
    && message.channelId === currentChannelId
    && verified.has(originKey(message.channelId!, message.clientMsgNo!))
}
