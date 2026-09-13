import i18n from '@/i18n';
import { chatMessagesApiService } from './chatMessagesApi';
import { useStaffDeliveryStore } from '@/stores/staffDeliveryStore';
import type { Message } from '@/types';
import type { AssistTrainingIntent, StaffDeliveryPayload } from '@/types/staffDelivery';

interface CustomerDeliveryOptions {
  staffId: string;
  channelId: string;
  channelType: number;
  clientMsgNo: string;
  payload: StaffDeliveryPayload;
  training?: AssistTrainingIntent | null;
  platformType?: string;
  isConnected: boolean;
  sendWsMessage: (channelId: string, channelType: number, payload: StaffDeliveryPayload, clientMsgNo: string) => Promise<unknown>;
  updateMessage: (clientMsgNo: string, patch: Partial<Message>) => void;
}

/** One customer delivery authority for text, attachments and upload retries. */
export async function sendCustomerMessage(options: CustomerDeliveryOptions): Promise<boolean> {
  const { staffId, channelId, channelType, clientMsgNo, payload, training, platformType, updateMessage } = options;
  if (channelType === 251) {
    const receipt = await useStaffDeliveryStore.getState().submit(staffId, {
      channel_id: channelId, channel_type: 251, client_msg_no: clientMsgNo, payload, training,
    });
    updateMessage(clientMsgNo, { metadata: {
      delivery_managed: true,
      delivery_status: receipt.delivery_status,
      ws_sent: receipt.delivery_status === 'sent',
      ws_send_error: false, platform_send_error: false,
    } });
    return receipt.delivery_status === 'sent';
  }

  // Non-customer channels retain their existing transport.
  if (platformType && platformType !== 'website') {
    await chatMessagesApiService.staffSendPlatformMessage({
      channel_id: channelId, channel_type: channelType, client_msg_no: clientMsgNo, payload,
    });
  }
  if (!options.isConnected) throw new Error(i18n.t('chat.input.errors.websocketFailed'));
  await options.sendWsMessage(channelId, channelType, payload, clientMsgNo);
  updateMessage(clientMsgNo, { metadata: { ws_sent: true, ws_send_error: false } });
  return true;
}
