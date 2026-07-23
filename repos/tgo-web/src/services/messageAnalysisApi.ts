import { BaseApiService } from './base/BaseApiService';
import type { CombinedMessageAnalysis } from '@/types/messageAnalysis';

interface MessageAnalysisBatchResponse {
  results: CombinedMessageAnalysis[];
}

class MessageAnalysisApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';

  protected readonly endpoints = {
    STAFF_BATCH: `/${this.apiVersion}/message-analysis/staff/messages/batch`,
  };

  async getStaffBatch(sourceMessageIds: string[]): Promise<CombinedMessageAnalysis[]> {
    if (sourceMessageIds.length === 0) return [];
    const response = await this.post<MessageAnalysisBatchResponse>(
      this.endpoints.STAFF_BATCH,
      { source_message_ids: sourceMessageIds.slice(0, 100) },
    );
    return response.results;
  }
}

export const messageAnalysisApi = new MessageAnalysisApiService();
