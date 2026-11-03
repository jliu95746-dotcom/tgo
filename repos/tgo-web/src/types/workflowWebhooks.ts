export interface WebhookReleaseContext { release_id: string; definition_digest: string; version: number }
export interface WorkflowWebhook {
  id: string; project_id: string; workflow_id: string; revision: number; credential_revision: number;
  key_id: string; enabled: boolean; release_id: string; release_digest: string;
  actor_staff_id: string; created_by: string; updated_by: string; created_at: string; updated_at: string;
}
export interface WebhookState {
  webhook: WorkflowWebhook | null; current_release: WebhookReleaseContext | null;
  binding_current: boolean; registration_available: boolean; execution_available: boolean; reason: string | null;
}
export interface WebhookChange { request_id: string; expected_revision: number }
export interface WebhookSave extends WebhookChange {
  expected_release_id: string; expected_release_digest: string; enabled: boolean;
}
export type WebhookCommand = { operation: 'save'; request: WebhookSave }
  | { operation: 'disable' | 'rotate'; request: WebhookChange };
export interface WebhookReceipt {
  request_id: string; operation: WebhookCommand['operation']; webhook: WorkflowWebhook; replayed: boolean;
  credential_status: 'not_issued' | 'available' | 'superseded' | 'unavailable'; credential: { secret: string } | null;
}
export interface WebhookDelivery {
  id: string; binding_revision: number; credential_revision: number; release_id: string;
  status: 'pending' | 'queued' | 'completed' | 'failed' | 'cancelled' | 'expired' | 'blocked';
  attempts: number; error_code: string | null; execution_id: string | null; execution_status: string | null;
  created_at: string; expires_at: string;
}
export interface WebhookDeliveryList {
  project_id: string; workflow_id: string; items: WebhookDelivery[]; total: number;
  limit: number; offset: number; has_more: boolean;
}
export interface WorkflowWebhookApi {
  getState(id: string, signal?: AbortSignal): Promise<WebhookState>;
  getHistory(id: string, offset: number, signal?: AbortSignal): Promise<WebhookDeliveryList>;
  save(id: string, request: WebhookSave, signal?: AbortSignal): Promise<WebhookReceipt>;
  disable(id: string, request: WebhookChange, signal?: AbortSignal): Promise<WebhookReceipt>;
  rotate(id: string, request: WebhookChange, signal?: AbortSignal): Promise<WebhookReceipt>;
  getCommand(id: string, request: string, signal?: AbortSignal): Promise<WebhookReceipt>;
}
export interface WebhookUiState {
  ownerKey: string | null; workflowId: string | null; state: WebhookState | null;
  history: WebhookDeliveryList | null; pending: WebhookCommand | null;
  credential: { secret: string; hookId: string; revision: number; keyId: string } | null;
  storageReady: boolean; loading: boolean; saving: boolean; error: string; historyError: string; notice: string;
}
