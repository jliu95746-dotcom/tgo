export interface ScriptSelectionWrite {
  request_id: string; expected_revision: number; enabled: boolean;
  report_id: string | null; review_id: string | null;
}

export interface ScriptSelectionRecord {
  scope: 'employee_device_script_selection'; can_enable: false;
  id: string; project_id: string; agent_id: string; request_id: string;
  configured_by: string; revision: number; enabled: boolean;
  binding_id: string; binding_revision: number; report_id: string; review_id: string;
  saved_at: string; expires_at: string; selection_digest: string;
}

export interface ScriptSelectionState {
  scope: 'employee_device_script_selection_state'; can_enable: false;
  project_id: string; actor_id: string; agent_id: string;
  selection: ScriptSelectionRecord | null;
  status: 'unselected' | 'selected' | 'changed' | 'expired' | 'revoked';
}
