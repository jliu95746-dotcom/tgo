export interface SessionCommandAlertItem {
  operation_id: string;
  project_id: string;
  kind: 'close' | 'transfer';
  notification_status: 'pending' | 'sending' | 'sent' | 'unknown' | 'skipped';
  effects_status: 'pending' | 'done' | 'unknown' | 'skipped';
  notification_attempts: number;
  effects_attempts: number;
  notification_error: string | null;
  effects_error: string | null;
  created_at: string;
  notification_updated_at: string;
}

export interface SessionCommandAlertReport {
  checked_at: string;
  stale_after_seconds: number;
  offset: number;
  total: number;
  items: SessionCommandAlertItem[];
}

export interface ReconcileSessionNotice {
  expected_status: 'sending' | 'unknown';
  reason: string;
}
