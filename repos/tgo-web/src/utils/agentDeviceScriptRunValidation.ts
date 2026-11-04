import { scriptUuid } from './agentDeviceScriptValidation';
import type { ScriptRunControl, ScriptRunView } from '@/types/agentDeviceScriptRuns';

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value));
const date = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);

export function validRunPointer(value: unknown): value is ScriptRunControl {
  return record(value) && Object.keys(value).sort().join() === 'report_id,request_id,review_id'
    && [value.request_id, value.report_id, value.review_id].every(scriptUuid);
}

export function validScriptRun(value: unknown, project: string, actor: string, agent: string, pointer: ScriptRunControl): value is ScriptRunView {
  const fields = 'request_id report_id review_id scope can_enable id project_id actor_id agent_id execution_id status error_code created_at deadline_at dispatch_started_at completed_at stop_requested_at result_digest result_fields'.split(' ');
  if (!record(value) || Object.keys(value).length !== fields.length || !fields.every(field => Object.prototype.hasOwnProperty.call(value, field))
    || value.scope !== 'employee_device_script_run' || value.can_enable !== false
    || value.project_id !== project || value.actor_id !== actor || value.agent_id !== agent || !scriptUuid(value.id)
    || value.request_id !== pointer.request_id || value.report_id !== pointer.report_id || value.review_id !== pointer.review_id
    || !['preparing', 'unknown', 'succeeded', 'failed', 'cancelled'].includes(String(value.status))
    || !date(value.created_at) || !date(value.deadline_at) || Date.parse(value.deadline_at) <= Date.parse(value.created_at)
    || value.execution_id !== null && !scriptUuid(value.execution_id)
    || value.result_digest !== null && !digest(value.result_digest)
    || ![value.completed_at, value.dispatch_started_at, value.stop_requested_at].every(item => item === null || date(item))
    || ![null, 'preparation_expired', 'not_dispatched', 'conditions_changed', 'result_unknown',
      'invalid_receipt', 'execution_failed', 'result_unavailable', 'cancelled'].includes(value.error_code as null | string)) return false;
  const terminal = ['succeeded', 'failed', 'cancelled'].includes(String(value.status));
  if (terminal !== (value.completed_at !== null)) return false;
  if (['unknown', 'succeeded'].includes(String(value.status)) && (!value.execution_id || !value.dispatch_started_at)) return false;
  if (value.result_fields === null) return true;
  if (value.status !== 'succeeded' || !digest(value.result_digest) || !record(value.result_fields)) return false;
  const entries = Object.entries(value.result_fields);
  return entries.length > 0 && entries.length <= 32
    && entries.every(([name, field]) => /^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(name)
      && (typeof field === 'boolean' || typeof field === 'number' && Number.isSafeInteger(field)
        || typeof field === 'string' && !/[\uD800-\uDFFF]/u.test(field)))
    && new TextEncoder().encode(JSON.stringify(value.result_fields)).length <= 65536;
}
