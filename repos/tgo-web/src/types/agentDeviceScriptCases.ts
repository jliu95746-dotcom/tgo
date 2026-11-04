import type { AgentScriptTrialStart, AgentScriptTrialReport, ScriptBusinessValues } from './agentDeviceScripts';

export interface AgentScriptCaseValues {
  parameters: ScriptBusinessValues;
  expected_fields: ScriptBusinessValues;
}
export interface AgentScriptCaseWrite extends AgentScriptTrialStart {
  case_id: string | null;
  expected_revision: number;
  title: string;
}
export interface AgentScriptCaseRecord {
  scope: 'employee_device_script_case'; can_enable: false;
  id: string; case_id: string; project_id: string; agent_id: string;
  created_by: string; request_id: string; revision: number; title: string;
  binding_id: string; binding_revision: number; device_id: string;
  policy_digest: string; grant_digest: string; case_digest: string;
  status: 'current' | 'changed' | 'superseded' | 'expired';
  created_at: string; expires_at: string;
}
export interface AgentScriptCaseRead { case: AgentScriptCaseRecord; values: AgentScriptCaseValues }
export interface AgentScriptCasePage {
  project_id: string; agent_id: string; items: AgentScriptCaseRecord[];
  total: number; skip: number; limit: number;
}
export interface AgentScriptCaseRun {
  scope: 'employee_device_script_case_trial'; can_enable: false;
  case_version_id: string; report: AgentScriptTrialReport;
}
export interface AgentScriptEvidence {
  scope: 'employee_device_script_evidence'; can_enable: false;
  project_id: string; actor_id: string; agent_id: string; report_id: string;
  status: 'available' | 'pending' | 'unavailable' | 'expired' | 'not_retained';
  case: AgentScriptCaseRecord | null; values: AgentScriptCaseValues | null;
  result_fields: ScriptBusinessValues | null; captured_at: string | null; expires_at: string | null;
}
/** Recovery metadata only. Never append input, expectation or result bodies. */
export interface ScriptCaseSavePointer {
  requestId: string; caseId: string | null; expectedRevision: number;
  bindingId: string; bindingRevision: number; deviceId: string;
  policyDigest: string; grantDigest: string;
}
export interface ScriptCaseSaveControl {
  request_id: string; binding_id: string; binding_revision: number;
  case_id: string | null; expected_revision: number;
}
export interface ScriptCaseSaveState extends ScriptCaseSaveControl {
  scope: 'employee_device_script_case_save'; can_enable: false;
  project_id: string; actor_id: string; agent_id: string;
  status: 'preparing' | 'saved' | 'failed' | 'cancelled';
  case: AgentScriptCaseRecord | null;
  created_at: string; deadline_at: string; completed_at: string | null;
  error_code: 'preparation_failed' | 'preparation_expired' | 'cancelled' | null;
}
export interface ScriptReviewWrite {
  case_version_id: string;
  case_digest: string;
  result_digest: string;
  decision: 'approved' | 'rejected';
}

export interface ScriptReviewRecord extends ScriptReviewWrite {
  id: string;
  reviewed_at: string;
  expires_at: string;
  revoked_at: string | null;
}

export interface ScriptReviewState {
  scope: 'employee_device_script_review';
  can_enable: false;
  project_id: string;
  actor_id: string;
  agent_id: string;
  report_id: string;
  status: 'unreviewed' | 'approved' | 'rejected' | 'revoked' | 'expired' | 'changed';
  review: ScriptReviewRecord | null;
}

export interface ScriptRunCheck {
  scope: 'employee_device_script_run_check';
  can_enable: false;
  review: ScriptReviewState;
  status: 'ready' | 'review_required' | 'device_changed' | 'budget_exhausted' | 'unavailable';
  remaining_runs: number | null;
  checked_at: string;
  valid_until: string | null;
}
