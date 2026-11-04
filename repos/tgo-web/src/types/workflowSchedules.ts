export interface SchedulePreviewRequest { cron_expression: string; timezone: string }
export interface SchedulePreview extends SchedulePreviewRequest { computed_at: string; preview_at: string[] }
export interface ScheduleReleaseContext extends SchedulePreviewRequest {
  release_id: string; definition_digest: string; version: number;
}
export interface WorkflowSchedule extends SchedulePreviewRequest {
  id: string; project_id: string; workflow_id: string; revision: number; enabled: boolean;
  release_id: string; release_digest: string; next_run_at: string | null;
  created_by: string; updated_by: string; created_at: string; updated_at: string;
}
export interface TimerRuntimeState {
  status: 'disabled' | 'key_missing' | 'unavailable' | 'invalid_probe' | 'scheduler_missing'
    | 'worker_waiting' | 'database_unavailable' | 'ready';
  checked_at: string; capability_enabled: boolean; ready: boolean;
  scheduler_recent: boolean; worker_recent: boolean; database_ready: boolean;
  scheduler_seen_at: string | null; worker_seen_at: string | null;
}
export interface ScheduleState {
  runtime?: TimerRuntimeState | null;
  schedule: WorkflowSchedule | null; current_release: ScheduleReleaseContext | null;
  binding_current: boolean; execution_available: boolean; preview_at: string[];
  suspension_reason: string | null; suspended_at: string | null;
}
export interface ScheduleDisable { request_id: string; expected_revision: number }
export interface ScheduleSave extends ScheduleDisable {
  expected_release_id: string; expected_release_digest: string; enabled: boolean;
}
export type ScheduleCommand = { operation: 'save'; request: ScheduleSave }
  | { operation: 'disable'; request: ScheduleDisable };
export interface ScheduleReceipt {
  request_id: string; operation: 'save' | 'disable'; replayed: boolean; schedule: WorkflowSchedule;
}
export interface ScheduleOccurrence {
  id: string; revision: number; scheduled_at: string;
  status: 'pending' | 'queued' | 'skipped' | 'cancelled' | 'blocked';
  attempts: number; error_code: string | null; last_failure_code: string | null;
  execution_id: string | null; execution_status: string | null; created_at: string;
}
export interface ScheduleOccurrenceList {
  items: ScheduleOccurrence[]; total: number; limit: number; offset: number; has_more: boolean;
}
export interface ScheduleUiState {
  ownerKey: string | null; workflowId: string | null; state: ScheduleState | null;
  history: ScheduleOccurrenceList | null; pending: ScheduleCommand | null;
  preview: SchedulePreview | null; loading: boolean; saving: boolean; previewing: boolean;
  error: string; historyError: string; previewError: string; notice: string;
}
export interface WorkflowScheduleApi {
  getState(id: string, signal?: AbortSignal): Promise<ScheduleState>;
  getHistory(id: string, offset: number, signal?: AbortSignal): Promise<ScheduleOccurrenceList>;
  preview(id: string, request: SchedulePreviewRequest, signal?: AbortSignal): Promise<SchedulePreview>;
  save(id: string, request: ScheduleSave, signal?: AbortSignal): Promise<ScheduleReceipt>;
  disable(id: string, request: ScheduleDisable, signal?: AbortSignal): Promise<ScheduleReceipt>;
  getCommand(id: string, request: string, signal?: AbortSignal): Promise<ScheduleReceipt>;
}
