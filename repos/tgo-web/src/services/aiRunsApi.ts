import { BaseApiService } from './base/BaseApiService';
import i18n from '@/i18n';

/**
 * Staff cancel request - for staff-facing cancel endpoint (JWT auth)
 */
export interface StaffCancelRequest {
  client_msg_no: string;
  reason?: string | null;
}

class AIRunsApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    cancel: '/v1/ai/runs/cancel',
  } as const;

  /**
   * Cancel a running supervisor agent execution by client_msg_no (Staff)
   * Uses JWT authentication
   * @param request - Cancel request with client_msg_no
   */
  async cancelByClientNo(request: StaffCancelRequest): Promise<void> {
    const response = await this.post<unknown>(this.endpoints.cancel, request);
    if (
      !response || typeof response !== 'object' ||
      !('accepted' in response) || response.accepted !== true ||
      !('status' in response) || response.status !== 'cancelled' ||
      !('client_msg_no' in response) || response.client_msg_no !== request.client_msg_no
    ) {
      throw new Error(i18n.t('errors.replyStopUnconfirmed'));
    }
  }
}

export const aiRunsApiService = new AIRunsApiService();

