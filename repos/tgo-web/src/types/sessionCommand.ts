export type SessionCommandKind = 'close' | 'transfer';

export interface SessionCommandReceipt {
  operation_id: string;
  session_id: string;
  visitor_id: string;
  to_staff_id: string | null;
  kind: SessionCommandKind;
  notification_status: 'pending' | 'sending' | 'sent' | 'unknown' | 'skipped';
  attempts: number;
  effects_status?: 'pending' | 'done' | 'unknown' | 'skipped';
  effects_attempts?: number;
}

export interface SessionCommandScope {
  projectId: string;
  actorId: string;
}

export interface PendingSessionCommand extends SessionCommandScope {
  operationId: string;
  visitorId: string;
  kind: SessionCommandKind;
  targetStaffId: string | null;
  createdAt: string;
}
