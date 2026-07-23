import type {
  StaffMessageAnalysisBatchRequest,
  StaffMessageAnalysisBatchResponse,
} from '@/types/messageAnalysis';

import { BaseApiService } from './base/BaseApiService';


class MessageAnalysisApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    staffQuery: '/v1/message-analysis/staff/messages/query',
  } as const;

  async queryStaffMessageAnalyses(
    request: StaffMessageAnalysisBatchRequest,
  ): Promise<StaffMessageAnalysisBatchResponse> {
    return this.post<StaffMessageAnalysisBatchResponse>(
      this.endpoints.staffQuery,
      request,
    );
  }
}

export const messageAnalysisApiService = new MessageAnalysisApiService();

export default messageAnalysisApiService;
