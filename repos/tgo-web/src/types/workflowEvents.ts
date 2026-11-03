import type { WorkflowExecution, WorkflowJsonValue } from './workflow';

export interface WorkflowEventTestContext {
  workflow_id: string;
  event_type: string;
  release_id: string;
  release_digest: string;
  version: number;
  status: 'active';
}

export interface WorkflowEventTestRequest {
  request_id: string;
  event_type: string;
  expected_release_id: string;
  expected_release_digest: string;
  data: Record<string, WorkflowJsonValue>;
}

export interface WorkflowEventAccepted {
  execution_id: string;
  status: string;
  deduplicated: boolean;
}

export interface WorkflowEventTestSession {
  context: WorkflowEventTestContext | null;
  jsonInput: string;
  attempt: WorkflowEventTestRequest | null;
  accepted: WorkflowEventAccepted | null;
  execution: WorkflowExecution | null;
  loadingContext: boolean;
  sending: boolean;
  refreshing: boolean;
  cancelling: boolean;
  rejected: boolean;
  error: string;
}

export interface WorkflowEventTestState {
  ownerKey: string | null;
  activeWorkflowId: string | null;
  sessions: Record<string, WorkflowEventTestSession>;
}

export interface WorkflowEventSubscription {
  workflow_id: string;
  subscription_id: string | null;
  event_type: 'staff.session.closed';
  enabled: boolean;
  revision: number;
  release_id: string | null;
  release_digest: string | null;
  version: number | null;
  enabled_at: string | null;
}

export interface WorkflowEventSubscriptionUpdate {
  enabled: boolean;
  expected_revision: number;
  expected_release_id?: string;
  expected_release_digest?: string;
}

export interface WorkflowEventDelivery {
  id: string;
  session_id: string;
  status: 'pending' | 'sending' | 'accepted' | 'failed' | 'cancelled';
  attempts: number;
  last_error: string | null;
  execution_id: string | null;
  created_at: string;
}

export interface WorkflowEventDeliveryList {
  items: WorkflowEventDelivery[];
}

export interface WorkflowEventSubscriptionState {
  ownerKey: string | null;
  workflowId: string | null;
  subscription: WorkflowEventSubscription | null;
  context: WorkflowEventTestContext | null;
  deliveries: WorkflowEventDelivery[];
  loading: boolean;
  saving: boolean;
  error: string;
  contextError: string;
  deliveryError: string;
}
