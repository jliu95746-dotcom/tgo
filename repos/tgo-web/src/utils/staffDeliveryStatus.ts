import type { StaffDeliveryReceipt } from '@/types/staffDelivery';

export function canReuseStaffDeliveryRequest(receipt: StaffDeliveryReceipt | undefined): boolean {
  return !(receipt?.delivery_transport === 'wechat_local' && receipt.delivery_status === 'failed');
}

export function shouldPollStaffDelivery(receipt: StaffDeliveryReceipt, accessDenied = false): boolean {
  if (accessDenied || receipt.delivery_status === 'failed') return false;
  if (receipt.delivery_transport === 'wechat_local' && receipt.delivery_status !== 'sent') return true;
  if (receipt.delivery_status === 'unknown') return false;
  return receipt.history_status !== 'sent' || receipt.training_status === 'pending';
}

export function staffDeliveryLabel(receipt: StaffDeliveryReceipt, accessDenied = false): string | null {
  if (accessDenied) return 'chat.delivery.accessChanged';
  const { delivery_status: status, history_status: history, training_status: training } = receipt;
  if (receipt.delivery_transport === 'wechat_local') {
    if (status === 'pending') return 'chat.delivery.wechatPending';
    if (status === 'processing') return 'chat.delivery.wechatProcessing';
    if (status === 'unknown') return 'chat.delivery.wechatUnknown';
    if (status === 'failed') return 'chat.delivery.wechatFailed';
    if (status === 'sent' && (!training || training === 'none')) {
      return history === 'sent' ? 'chat.delivery.wechatSent' : 'chat.delivery.wechatHistoryPending';
    }
  }
  if (status === 'sent') {
    if (history !== 'sent') return 'chat.delivery.historyPending';
    if (training === 'saved') return 'chat.delivery.trainingSaved';
    if (training === 'unavailable') return 'chat.delivery.trainingUnavailable';
    if (training === 'pending') return 'chat.delivery.trainingPending';
    return null;
  }
  return status === 'failed' ? 'chat.delivery.failed'
    : status === 'unknown' ? 'chat.delivery.unknown' : 'chat.delivery.processing';
}
