import { BaseApiService } from './base/BaseApiService';
import apiClient from './api';
import i18n from '@/i18n';
import type {
  AIExecutionAttemptLookup,
  AIExecutionAttemptSummary,
  AIExecutionToolEventSummary,
  AIExecutionBusinessQuerySummary,
  AIReplyOriginRequest,
  AIReplyOriginEvidence,
} from '@/types/aiRuns';
import type { WorkflowBusinessQueryAuditList } from '@/types/workflow';

/**
 * Staff cancel request - for staff-facing cancel endpoint (JWT auth)
 */
export interface StaffCancelRequest {
  client_msg_no: string;
  reason?: string | null;
}

export const AI_EXECUTION_ATTEMPT_PAGE_SIZE = 20;
export const AI_EXECUTION_TOOL_EVENT_PAGE_SIZE = 50;
export const AI_EXECUTION_BUSINESS_QUERY_PAGE_SIZE = 50;
export const AI_EXECUTION_AUDIT_OBSERVATION_PAGE_SIZE = 20;

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

  async listChannelMessageAttempts(
    lookup: AIExecutionAttemptLookup,
    skip = 0,
    signal?: AbortSignal,
  ): Promise<AIExecutionAttemptSummary[]> {
    return apiClient.post<AIExecutionAttemptSummary[]>(
      `/v1/ai/runs/attempts/lookup-channel-message?skip=${skip}&limit=${AI_EXECUTION_ATTEMPT_PAGE_SIZE}`,
      lookup,
      { signal },
    );
  }

  async listToolEvents(
    executionId: string,
    skip = 0,
    signal?: AbortSignal,
  ): Promise<AIExecutionToolEventSummary[]> {
    return apiClient.get<AIExecutionToolEventSummary[]>(
      `/v1/ai/runs/attempts/${encodeURIComponent(executionId)}/tool-events?skip=${skip}&limit=${AI_EXECUTION_TOOL_EVENT_PAGE_SIZE}`,
      { signal },
    );
  }

  async listBusinessQueries(
    executionId: string,
    skip = 0,
    signal?: AbortSignal,
  ): Promise<AIExecutionBusinessQuerySummary[]> {
    return apiClient.get<AIExecutionBusinessQuerySummary[]>(
      `/v1/ai/runs/attempts/${encodeURIComponent(executionId)}/business-queries?skip=${skip}&limit=${AI_EXECUTION_BUSINESS_QUERY_PAGE_SIZE}`,
      { signal },
    );
  }

  async listBusinessAuditObservations(
    executionId: string,
    skip = 0,
    signal?: AbortSignal,
  ): Promise<WorkflowBusinessQueryAuditList> {
    return apiClient.get<WorkflowBusinessQueryAuditList>(
      `/v1/ai/runs/attempts/${encodeURIComponent(executionId)}/business-audit-observations?skip=${skip}&limit=${AI_EXECUTION_AUDIT_OBSERVATION_PAGE_SIZE}`,
      { signal },
    );
  }

  async verifyReplyOrigins(
    request: AIReplyOriginRequest,
    signal?: AbortSignal,
  ): Promise<AIReplyOriginEvidence[]> {
    return apiClient.post<AIReplyOriginEvidence[]>(
      '/v1/ai/runs/reply-origins/verify', request, { signal },
    );
  }
}

export const aiRunsApiService = new AIRunsApiService();
