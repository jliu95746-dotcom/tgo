/**
 * Workflow API Service
 * Handles all communication with the backend for AI Agent Workflows
 * Strictly follows the OpenAPI specification in docs/api.json
 */

import apiClient from './api';
import i18n from '@/i18n';
import type { WorkflowEventAccepted, WorkflowEventTestContext, WorkflowEventTestRequest,
  WorkflowEventSubscription, WorkflowEventSubscriptionUpdate, WorkflowEventDeliveryList } from '@/types/workflowEvents';
import type { 
  Workflow, 
  WorkflowListResponse,
  WorkflowDeviceReferences,
  WorkflowCreateRequest,
  WorkflowUpdateRequest,
  WorkflowExecution,
  WorkflowCustomerMessageLookup,
  WorkflowChannelMessageLookup,
  WorkflowQueryParams,
  WorkflowJsonValue,
  WorkflowStreamEvent,
  WorkflowCapabilities,
  WorkflowBusinessQueryAuditList,
  WorkflowPublishRequest,
  WorkflowReleaseSummary,
  WorkflowRollbackRequest,
  WorkflowApprovalSummary,
  WorkflowReconciliationSummary,
  WorkflowReconciliationDecisionResult,
  WorkflowApprovalDecisionResult,
  WorkflowAfterSalesReviewSummary,
  WorkflowAfterSalesReviewResult,
  WorkflowAfterSalesEvidenceFile,
  WorkflowAfterSalesChatTarget,
  WorkflowAfterSalesIntakeCreate,
  WorkflowAfterSalesIntakeSummary,
  WorkflowAfterSalesHandoffSummary,
} from '@/types/workflow';

export const WORKFLOW_EXECUTION_PAGE_SIZE = 20;

export type WorkflowExecutionCursor = Pick<WorkflowExecution, 'id' | 'started_at'>;

function executionPageQuery(skip: number, cursor: WorkflowExecutionCursor | null): string {
  const params = new URLSearchParams({ limit: String(WORKFLOW_EXECUTION_PAGE_SIZE) });
  if (cursor) {
    params.set('before_started_at', cursor.started_at);
    params.set('before_id', cursor.id);
  } else {
    params.set('skip', String(skip));
  }
  return params.toString();
}

export class WorkflowApiService {
  private static BASE_PATH = '/v1/ai/workflows';

  static async getBusinessQueryAudits(
    queryId: string, signal?: AbortSignal,
  ): Promise<WorkflowBusinessQueryAuditList> {
    return apiClient.get<WorkflowBusinessQueryAuditList>(
      `${this.BASE_PATH}/business-queries/${encodeURIComponent(queryId)}/audits`,
      { signal },
    );
  }

  static async listDeviceReferences(deviceId: string, signal?: AbortSignal): Promise<WorkflowDeviceReferences> {
    return apiClient.get<WorkflowDeviceReferences>(
      `${this.BASE_PATH}/device-references?device_id=${encodeURIComponent(deviceId)}&limit=5`,
      { signal },
    );
  }

  static async getCapabilities(): Promise<WorkflowCapabilities> {
    return apiClient.get<WorkflowCapabilities>(`${this.BASE_PATH}/capabilities`);
  }

  static async getEventTestContext(id: string, signal?: AbortSignal): Promise<WorkflowEventTestContext> {
    return apiClient.get<WorkflowEventTestContext>(
      `${this.BASE_PATH}/${encodeURIComponent(id)}/event-test-context`, { signal },
    );
  }

  static async sendEventTest(id: string, request: WorkflowEventTestRequest, signal?: AbortSignal): Promise<WorkflowEventAccepted> {
    return apiClient.post<WorkflowEventAccepted>(
      `${this.BASE_PATH}/${encodeURIComponent(id)}/event-tests`, request, { signal },
    );
  }

  static async getEventSubscription(id: string, signal?: AbortSignal): Promise<WorkflowEventSubscription> {
    return apiClient.get<WorkflowEventSubscription>(
      `${this.BASE_PATH}/${encodeURIComponent(id)}/event-subscription`, { signal },
    );
  }

  static async updateEventSubscription(id: string, request: WorkflowEventSubscriptionUpdate, signal?: AbortSignal): Promise<WorkflowEventSubscription> {
    if (!request.enabled) {
      return apiClient.delete<WorkflowEventSubscription>(
        `${this.BASE_PATH}/${encodeURIComponent(id)}/event-subscription?expected_revision=${request.expected_revision}`, { signal },
      );
    }
    return apiClient.put<WorkflowEventSubscription>(
      `${this.BASE_PATH}/${encodeURIComponent(id)}/event-subscription`, request, { signal },
    );
  }

  static async listEventDeliveries(id: string, signal?: AbortSignal): Promise<WorkflowEventDeliveryList> {
    return apiClient.get<WorkflowEventDeliveryList>(
      `${this.BASE_PATH}/${encodeURIComponent(id)}/event-deliveries`, { signal },
    );
  }

  static async listApprovals(): Promise<WorkflowApprovalSummary[]> {
    return apiClient.get<WorkflowApprovalSummary[]>(`${this.BASE_PATH}/approvals`);
  }

  static async getApprovalChatTarget(
    approvalId: string,
  ): Promise<WorkflowAfterSalesChatTarget> {
    return apiClient.get<WorkflowAfterSalesChatTarget>(
      `${this.BASE_PATH}/approvals/${encodeURIComponent(approvalId)}/chat-target`,
    );
  }

  static async listAftersalesReviews(
    limit = 30, cursor: Pick<WorkflowAfterSalesReviewSummary, 'status' | 'created_at' | 'id'> | null = null,
  ): Promise<WorkflowAfterSalesReviewSummary[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) {
      params.set('before_status', cursor.status);
      params.set('before_created_at', cursor.created_at);
      params.set('before_id', cursor.id);
    }
    return apiClient.get<WorkflowAfterSalesReviewSummary[]>(
      `${this.BASE_PATH}/aftersales-reviews?${params}`,
    );
  }

  static async listAftersalesReviewFiles(
    reviewId: string,
  ): Promise<WorkflowAfterSalesEvidenceFile[]> {
    return apiClient.get<WorkflowAfterSalesEvidenceFile[]>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/files`,
    );
  }

  static async getAftersalesChatTarget(
    reviewId: string,
  ): Promise<WorkflowAfterSalesChatTarget> {
    return apiClient.get<WorkflowAfterSalesChatTarget>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/chat-target`,
    );
  }

  static async getAftersalesIntake(
    reviewId: string,
  ): Promise<WorkflowAfterSalesIntakeSummary | null> {
    return apiClient.get<WorkflowAfterSalesIntakeSummary | null>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/intake`,
    );
  }

  static async prepareAftersalesIntake(
    reviewId: string, request: WorkflowAfterSalesIntakeCreate,
  ): Promise<WorkflowAfterSalesIntakeSummary> {
    return apiClient.post<WorkflowAfterSalesIntakeSummary>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/intake`,
      request,
    );
  }

  static async getAftersalesHandoff(
    reviewId: string,
  ): Promise<WorkflowAfterSalesHandoffSummary | null> {
    return apiClient.get<WorkflowAfterSalesHandoffSummary | null>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/handoff`,
    );
  }

  static async requestAftersalesHandoff(
    reviewId: string,
  ): Promise<WorkflowAfterSalesHandoffSummary> {
    return apiClient.post<WorkflowAfterSalesHandoffSummary>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/handoff`,
    );
  }

  static async decideAftersalesReview(
    reviewId: string, result: WorkflowAfterSalesReviewResult, reviewDigest: string,
  ): Promise<WorkflowAfterSalesReviewSummary> {
    return apiClient.post<WorkflowAfterSalesReviewSummary>(
      `${this.BASE_PATH}/aftersales-reviews/${encodeURIComponent(reviewId)}/decision`,
      { result, review_digest: reviewDigest },
    );
  }

  static async decideApproval(
    approvalId: string, decision: 'approve' | 'reject', previewDigest: string,
  ): Promise<WorkflowApprovalDecisionResult> {
    return apiClient.post<WorkflowApprovalDecisionResult>(
      `${this.BASE_PATH}/approvals/${encodeURIComponent(approvalId)}/decision`,
      { decision, preview_digest: previewDigest },
    );
  }

  /**
   * List all workflows with pagination and filtering
   */
  static async listWorkflows(params: WorkflowQueryParams = {}): Promise<WorkflowListResponse> {
    const query = new URLSearchParams();
    if (params.status) query.append('status', params.status);
    if (params.search) query.append('search', params.search);
    if (params.limit) query.append('limit', params.limit.toString());
    if (params.offset) query.append('offset', params.offset.toString());
    if (params.tags && params.tags.length > 0) {
      params.tags.forEach(tag => query.append('tags', tag));
    }

    const queryString = query.toString();
    const endpoint = queryString ? `${this.BASE_PATH}?${queryString}` : this.BASE_PATH;
    
    return apiClient.get<WorkflowListResponse>(endpoint);
  }

  /**
   * Get a single workflow by ID
   */
  static async getWorkflow(id: string): Promise<Workflow> {
    return apiClient.get<Workflow>(`${this.BASE_PATH}/${id}`);
  }

  /**
   * Create a new workflow
   */
  static async createWorkflow(request: WorkflowCreateRequest): Promise<Workflow> {
    return apiClient.post<Workflow>(this.BASE_PATH, request);
  }

  /**
   * Update an existing workflow
   */
  static async updateWorkflow(id: string, request: WorkflowUpdateRequest): Promise<Workflow> {
    return apiClient.put<Workflow>(`${this.BASE_PATH}/${id}`, request);
  }

  /**
   * Delete a workflow
   */
  static async deleteWorkflow(id: string): Promise<void> {
    return apiClient.delete(`${this.BASE_PATH}/${id}`);
  }

  /**
   * Duplicate an existing workflow
   */
  static async duplicateWorkflow(id: string, name?: string): Promise<Workflow> {
    return apiClient.post<Workflow>(`${this.BASE_PATH}/${id}/duplicate`, { name });
  }

  /**
   * Validate a workflow's structure and data
   */
  static async validateWorkflow(id: string): Promise<{ valid: boolean; errors: string[] }> {
    return apiClient.post<{ valid: boolean; errors: string[] }>(`${this.BASE_PATH}/${id}/validate`);
  }

  /**
   * Publish a workflow to make it active
   */
  static async publishWorkflow(id: string, request: WorkflowPublishRequest): Promise<Workflow> {
    return apiClient.post<Workflow>(`${this.BASE_PATH}/${id}/publish`, request);
  }

  static async getReleases(id: string): Promise<WorkflowReleaseSummary[]> {
    return apiClient.get<WorkflowReleaseSummary[]>(`${this.BASE_PATH}/${id}/releases`);
  }

  static async rollbackWorkflow(id: string, request: WorkflowRollbackRequest): Promise<Workflow> {
    return apiClient.post<Workflow>(`${this.BASE_PATH}/${id}/rollback`, request);
  }

  /**
   * Execute a workflow
   */
  static async executeWorkflow(id: string, input?: Record<string, any>): Promise<WorkflowExecution> {
    return apiClient.post<WorkflowExecution>(`${this.BASE_PATH}/${id}/execute`, { inputs: input });
  }

  static async executeWorkflowAsync(
    id: string, input: Record<string, WorkflowJsonValue>, signal?: AbortSignal,
  ): Promise<WorkflowExecution> {
    return apiClient.post<WorkflowExecution>(`${this.BASE_PATH}/${id}/execute`, {
      inputs: input, execution_mode: 'draft_test', async: true, stream: false,
    }, { signal });
  }

  /**
   * Get an execution's status and results
   */
  static async listReconciliations(
    cursor: Pick<WorkflowReconciliationSummary, 'id' | 'requested_at'> | null = null,
  ): Promise<WorkflowReconciliationSummary[]> {
    const params = new URLSearchParams({ limit: '30' });
    if (cursor) {
      params.set('before_requested_at', cursor.requested_at);
      params.set('before_id', cursor.id);
    }
    return apiClient.get<WorkflowReconciliationSummary[]>(`${this.BASE_PATH}/reconciliations?${params}`);
  }

  static async getReconciliation(approvalId: string): Promise<WorkflowReconciliationSummary> {
    return apiClient.get<WorkflowReconciliationSummary>(
      `${this.BASE_PATH}/reconciliations/${encodeURIComponent(approvalId)}`,
    );
  }

  static async decideReconciliation(
    approvalId: string, decision: 'continue' | 'stop', reviewDigest: string,
  ): Promise<WorkflowReconciliationDecisionResult> {
    return apiClient.post<WorkflowReconciliationDecisionResult>(
      `${this.BASE_PATH}/reconciliations/${encodeURIComponent(approvalId)}/decision`,
      { decision, review_digest: reviewDigest },
    );
  }

  static async getReconciliationDecision(
    approvalId: string, reviewDigest: string,
  ): Promise<WorkflowReconciliationDecisionResult> {
    return apiClient.get<WorkflowReconciliationDecisionResult>(
      `${this.BASE_PATH}/reconciliations/${encodeURIComponent(approvalId)}/decisions/${encodeURIComponent(reviewDigest)}`,
    );
  }

  static async getExecution(executionId: string, signal?: AbortSignal): Promise<WorkflowExecution> {
    return apiClient.get<WorkflowExecution>(`${this.BASE_PATH}/executions/${executionId}`, { signal });
  }

  /**
   * Cancel a running execution
   */
  static async cancelExecution(executionId: string, signal?: AbortSignal): Promise<void> {
    await apiClient.post(`${this.BASE_PATH}/executions/${executionId}/cancel`, undefined, { signal });
  }

  /**
   * List executions for a specific workflow
   */
  static async listExecutions(workflowId: string, skip = 0, cursor: WorkflowExecutionCursor | null = null): Promise<WorkflowExecution[]> {
    return apiClient.get<WorkflowExecution[]>(`${this.BASE_PATH}/${workflowId}/executions?${executionPageQuery(skip, cursor)}`);
  }

  static async listBusinessRequestExecutions(businessRequestId: string, skip = 0, cursor: WorkflowExecutionCursor | null = null): Promise<WorkflowExecution[]> {
    return apiClient.get<WorkflowExecution[]>(
      `${this.BASE_PATH}/executions/by-business-request/${encodeURIComponent(businessRequestId)}?${executionPageQuery(skip, cursor)}`,
    );
  }

  static async listVisitorSessionExecutions(visitorSessionId: string, skip = 0, cursor: WorkflowExecutionCursor | null = null): Promise<WorkflowExecution[]> {
    return apiClient.get<WorkflowExecution[]>(
      `${this.BASE_PATH}/executions/by-visitor-session/${encodeURIComponent(visitorSessionId)}?${executionPageQuery(skip, cursor)}`,
    );
  }

  static async listChildExecutions(parentExecutionId: string, skip = 0, cursor: WorkflowExecutionCursor | null = null): Promise<WorkflowExecution[]> {
    return apiClient.get<WorkflowExecution[]>(
      `${this.BASE_PATH}/executions/by-parent/${encodeURIComponent(parentExecutionId)}?${executionPageQuery(skip, cursor)}`,
    );
  }

  static async listCustomerMessageExecutions(request: WorkflowCustomerMessageLookup, skip = 0, cursor: WorkflowExecutionCursor | null = null): Promise<WorkflowExecution[]> {
    return apiClient.post<WorkflowExecution[]>(
      `${this.BASE_PATH}/executions/lookup-customer-message?${executionPageQuery(skip, cursor)}`, request,
    );
  }

  static async listChannelMessageExecutions(request: WorkflowChannelMessageLookup, skip = 0, cursor: WorkflowExecutionCursor | null = null): Promise<WorkflowExecution[]> {
    return apiClient.post<WorkflowExecution[]>(
      `${this.BASE_PATH}/executions/lookup-channel-message?${executionPageQuery(skip, cursor)}`, request,
    );
  }

  /**
   * Execute a workflow using streaming SSE events
   */
  static async executeWorkflowStream(
    id: string, 
    input: Record<string, WorkflowJsonValue>,
    onEvent: (event: WorkflowStreamEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    let receivedTerminal = false;
    await apiClient.stream(
      `${this.BASE_PATH}/${id}/execute`,
      { 
        inputs: input, 
        execution_mode: 'draft_test',
        stream: true 
      },
      {
        onMessage: (event, data) => {
          const workflowEvent = { event, ...data } as WorkflowStreamEvent;
          if (workflowEvent.event === 'workflow_finished') receivedTerminal = true;
          onEvent(workflowEvent);
        },
        signal
      }
    );
    if (!receivedTerminal && !signal?.aborted) {
      throw new Error(i18n.t('workflow.debug.streamInterrupted', '调试连接已中断，请刷新查看执行结果。'));
    }
  }

  /**
   * Get available variables for a node in a workflow
   */
  static async getAvailableVariables(workflowId: string): Promise<any> {
    return apiClient.get(`${this.BASE_PATH}/${workflowId}/variables`);
  }
}
