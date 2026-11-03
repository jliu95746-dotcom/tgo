/** Configuration and machine comparisons never grant employee approval. */
export type ScriptBusinessValues = Record<string, string | number | boolean>;

export interface AgentScriptBinding {
  scope: 'employee_device_script_configuration';
  can_enable: false;
  id: string; project_id: string; agent_id: string; device_id: string;
  script_id: string; policy_digest: string; grant_digest: string;
  enabled: boolean; revision: number; configured_by: string;
}
export interface AgentScriptBindingState {
  project_id: string; agent_id: string; binding: AgentScriptBinding | null;
}
export interface AgentScriptBindingWrite {
  request_id: string; expected_revision: number; enabled: boolean;
  device_id?: string; expected_policy_digest?: string;
}
export interface AgentScriptTrialStart {
  request_id: string; binding_id: string; binding_revision: number;
  parameters: ScriptBusinessValues; expected_fields: ScriptBusinessValues;
}
export interface AgentScriptTrialReport {
  scope: 'employee_device_script_trial'; can_enable: false; assessment: 'not_reviewed';
  id: string; project_id: string; actor_id: string; agent_id: string;
  request_id: string; device_id: string; execution_id: string;
  binding_id: string; binding_revision: number;
  policy_digest: string; grant_digest: string; expected_digest: string; result_digest: string | null;
  status: 'unknown' | 'matched' | 'mismatched' | 'failed';
  error_code: 'result_unknown' | 'configuration_changed' | 'connection_changed' | 'invalid_receipt'
    | 'result_unavailable' | 'execution_failed' | 'interrupted' | null;
  remaining_runs: number | null; applicability: 'current' | 'changed' | 'expired' | 'unknown';
  created_at: string; completed_at: string | null; expires_at: string; dispatch_claimed_at: string;
}
export interface ScriptTrialPointer {
  requestId: string; bindingId: string; bindingRevision: number; deviceId: string;
  policyDigest: string; grantDigest: string;
}
export interface ScriptBindingPointer {
  requestId: string; expectedRevision: number; enabled: boolean;
  deviceId: string | null; policyDigest: string | null;
}

export interface ScriptTrialRequestControl {
  request_id: string; binding_id: string; binding_revision: number;
}
export interface ScriptTrialRequestState extends ScriptTrialRequestControl {
  scope: 'employee_device_script_trial_request'; can_enable: false;
  project_id: string; actor_id: string; agent_id: string;
  status: 'preparing' | 'ready' | 'failed' | 'cancelled';
  stop_status: 'none' | 'requested' | 'confirmed' | 'finished';
  created_at: string; deadline_at: string; completed_at: string | null;
  error_code: 'preparation_timeout' | 'preparation_failed' | 'interrupted' | 'cancelled' | null;
  report: AgentScriptTrialReport | null;
}
