import type { Message } from '@/types';
import type { WorkflowChannelMessageLookup } from '@/types/workflow';

export interface WorkflowMessageNavigation {
  workflowMessageLookup: WorkflowChannelMessageLookup;
}

const customerChannelPattern = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-vtr$/i;

export function isCustomerServiceChannelId(value: string): boolean {
  return customerChannelPattern.test(value);
}

function isChannelMessageLookup(value: unknown): value is WorkflowChannelMessageLookup {
  if (typeof value !== 'object' || value === null) return false;
  const lookup = value as Record<string, unknown>;
  return typeof lookup.channel_id === 'string'
    && isCustomerServiceChannelId(lookup.channel_id)
    && typeof lookup.source_message_id === 'string'
    && lookup.source_message_id.trim().length > 0
    && lookup.source_message_id.length <= 255;
}

export function customerWorkflowMessageLookup(
  message: Pick<Message, 'type' | 'channelType' | 'channelId' | 'sourceMessageId'>,
): WorkflowChannelMessageLookup | null {
  if (message.type !== 'visitor' || message.channelType !== 251) return null;
  const lookup = {
    channel_id: message.channelId,
    source_message_id: message.sourceMessageId?.trim(),
  };
  return isChannelMessageLookup(lookup) ? lookup : null;
}

export function isWorkflowMessageNavigation(value: unknown): value is WorkflowMessageNavigation {
  if (typeof value !== 'object' || value === null) return false;
  return isChannelMessageLookup((value as Record<string, unknown>).workflowMessageLookup);
}
