import { create } from 'zustand';
import { chatMessagesApiService } from '@/services/chatMessagesApi';
import type { StaffDeliveryReceipt, StaffDeliveryRequest, StaffDeliveryRecord } from '@/types/staffDelivery';

interface DeliveryEntry {
  request: StaffDeliveryRequest;
  receipt: StaffDeliveryReceipt;
}

interface StaffDeliveryState {
  entries: Record<string, DeliveryEntry>;
  submit: (staffId: string, request: StaffDeliveryRequest) => Promise<StaffDeliveryReceipt>;
  refresh: (staffId: string, clientMsgNo: string) => Promise<StaffDeliveryReceipt | undefined>;
  restore: (staffId: string, channelId: string) => Promise<StaffDeliveryRecord[]>;
}

export const deliveryKey = (staffId: string, clientMsgNo: string): string => `${staffId}:${clientMsgNo}`;

// Runtime promises are not persisted. Delivery authority and recovery live on the server.
const inFlight = new Map<string, Promise<StaffDeliveryReceipt>>();

export const useStaffDeliveryStore = create<StaffDeliveryState>((set, get) => {
  const save = (key: string, request: StaffDeliveryRequest, receipt: StaffDeliveryReceipt) => {
    set(state => ({ entries: { ...state.entries, [key]: { request, receipt } } }));
    return receipt;
  };
  const unknownReceipt = (request: StaffDeliveryRequest): StaffDeliveryReceipt => ({
    client_msg_no: request.client_msg_no,
    delivery_status: 'unknown', history_status: 'pending',
    training_status: request.training ? 'pending' : 'none',
    training_skill_name: request.training?.skill_name,
  });
  const refresh = async (staffId: string, clientMsgNo: string) => {
    const key = deliveryKey(staffId, clientMsgNo);
    const entry = get().entries[key];
    if (!entry) return undefined;
    try {
      return save(key, entry.request, await chatMessagesApiService.getStaffDelivery(entry.request));
    } catch {
      // Failure to read a receipt is not evidence of failed delivery.
      return entry.receipt;
    }
  };
  return {
    entries: {}, refresh,
    restore: async (staffId, channelId) => {
      const restored: StaffDeliveryRecord[] = [];
      let after = '';
      while (true) {
        const page = await chatMessagesApiService.getPendingStaffDeliveries(channelId, after);
        for (const entry of page) {
          const key = deliveryKey(staffId, entry.request.client_msg_no);
          if (!get().entries[key]) save(key, entry.request, entry.receipt);
        }
        restored.push(...page);
        if (page.length < 100) break;
        after = page[page.length - 1].request.client_msg_no;
      }
      return restored;
    },
    submit: async (staffId, request) => {
      const key = deliveryKey(staffId, request.client_msg_no);
      const previous = get().entries[key];
      if (previous && (
        previous.request.channel_id !== request.channel_id
        || previous.request.channel_type !== request.channel_type
        || JSON.stringify(previous.request.payload) !== JSON.stringify(request.payload)
        || JSON.stringify(previous.request.training || null) !== JSON.stringify(request.training || null)
      )) throw new Error('STAFF_MESSAGE_IDENTITY_CONFLICT');
      const running = inFlight.get(key);
      if (running) return running;
      if (previous?.receipt.delivery_status === 'sent') return previous.receipt;
      if (previous && previous.receipt.delivery_status !== 'failed') {
        return (await refresh(staffId, request.client_msg_no)) || previous.receipt;
      }

      save(key, request, { ...unknownReceipt(request), delivery_status: 'processing' });
      const task = (async () => {
        try {
          return save(key, request, await chatMessagesApiService.deliverStaffMessage({
            ...request, retry_failed: previous?.receipt.delivery_status === 'failed',
          }));
        } catch (error) {
          // A response may have been lost after acceptance. Reconcile using GET only.
          try {
            return save(key, request, await chatMessagesApiService.getStaffDelivery(request));
          } catch {
            const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined;
            const rejected = typeof status === 'number' && [400, 401, 403, 404, 413, 415, 422].includes(status);
            return save(key, request, {
              ...unknownReceipt(request),
              delivery_status: rejected ? 'failed' : 'unknown',
              error_message: rejected && error instanceof Error ? error.message : undefined,
            });
          }
        }
      })();
      inFlight.set(key, task);
      try { return await task; } finally { inFlight.delete(key); }
    },
  };
});
