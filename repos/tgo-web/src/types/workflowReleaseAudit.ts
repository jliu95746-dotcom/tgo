export interface WorkflowReleaseAuditItem {
  binding_id: string;
  agent_id: string;
  agent_name: string;
  agent_active: boolean;
  agent_default: boolean;
  workflow_id: string;
  workflow_enabled: boolean;
  workflow_name: string | null;
  workflow_status: string | null;
  current_published_version: number | null;
  details_available: boolean;
}

export interface WorkflowPinTransitionCommand {
  request_id: string;
  expected_binding_id: string;
  release_version: number;
  confirmed_disabled: true;
}

export interface WorkflowPinTransitionReceipt {
  id: string;
  request_id: string;
  agent_id: string;
  binding_id: string;
  workflow_id: string;
  release_version: number;
  release_id: string;
  release_digest: string;
  requested_by: string;
  created_at: string;
}

export interface WorkflowPinTransitionHistory {
  data: WorkflowPinTransitionReceipt[];
  pagination: WorkflowReleaseAuditResponse['pagination'];
}

export interface WorkflowReleaseAuditResponse {
  data: WorkflowReleaseAuditItem[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_next: boolean;
    has_prev: boolean;
  };
}
