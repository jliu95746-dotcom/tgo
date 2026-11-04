import type { ScriptBusinessValues } from './agentDeviceScripts';

export interface ScriptRunControl {
  request_id: string;
  report_id: string;
  review_id: string;
}
export interface ScriptRunStart extends ScriptRunControl {
  parameters: ScriptBusinessValues;
}
export interface ScriptRunView extends ScriptRunControl {
  scope: 'employee_device_script_run';
  can_enable: false;
  id: string;
  project_id: string;
  actor_id: string;
  agent_id: string;
  execution_id: string | null;
  status: 'preparing' | 'unknown' | 'succeeded' | 'failed' | 'cancelled';
  error_code: 'preparation_expired' | 'not_dispatched' | 'conditions_changed'
    | 'result_unknown' | 'invalid_receipt' | 'execution_failed' | 'result_unavailable' | 'cancelled' | null;
  created_at: string;
  deadline_at: string;
  dispatch_started_at: string | null;
  completed_at: string | null;
  stop_requested_at: string | null;
  result_digest: string | null;
  result_fields: ScriptBusinessValues | null;
}
export type ScriptRunMetadata = Omit<ScriptRunView, 'result_fields'>;
