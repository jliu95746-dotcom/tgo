import type { AgentScriptBinding, AgentScriptTrialReport, ScriptTrialPointer, ScriptBusinessValues, ScriptTrialRequestState } from '@/types/agentDeviceScripts';
import type { ScriptParameter } from '@/types/deviceScriptPolicy';

export const scriptUuid = (value: unknown): value is string => typeof value === 'string'
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value: Record<string, unknown>, keys: string) => {
  const expected = keys.split(' ');
  return expected.length === Object.keys(value).length && expected.every(key => Object.prototype.hasOwnProperty.call(value, key));
};
export function validScriptBinding(value: unknown, project: string, agent: string): value is AgentScriptBinding {
  return object(value) && exact(value, 'scope can_enable id project_id agent_id device_id script_id policy_digest grant_digest enabled revision configured_by')
    && value.scope === 'employee_device_script_configuration' && value.can_enable === false
    && value.project_id === project && value.agent_id === agent
    && [value.id, value.device_id, value.configured_by].every(scriptUuid)
    && typeof value.script_id === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(value.script_id)
    && digest(value.policy_digest) && digest(value.grant_digest) && typeof value.enabled === 'boolean'
    && typeof value.revision === 'number' && Number.isInteger(value.revision) && value.revision >= 1 && value.revision <= 2147483647;
}
export function validScriptReport(value: unknown, project: string, actor: string, agent: string, pointer: ScriptTrialPointer): value is AgentScriptTrialReport {
  if (!object(value) || !exact(value, 'scope can_enable assessment id project_id actor_id agent_id request_id device_id execution_id binding_id binding_revision policy_digest grant_digest expected_digest result_digest status error_code remaining_runs applicability created_at completed_at expires_at dispatch_claimed_at')
    || value.scope !== 'employee_device_script_trial' || value.can_enable !== false || value.assessment !== 'not_reviewed'
    || value.project_id !== project || value.actor_id !== actor || value.agent_id !== agent
    || value.request_id !== pointer.requestId || value.binding_id !== pointer.bindingId
    || value.binding_revision !== pointer.bindingRevision || value.device_id !== pointer.deviceId
    || value.policy_digest !== pointer.policyDigest || value.grant_digest !== pointer.grantDigest
    || ![value.id, value.execution_id].every(scriptUuid)
    || ![value.policy_digest, value.grant_digest, value.expected_digest].every(digest)
    || value.result_digest !== null && !digest(value.result_digest)
    || !['unknown', 'matched', 'mismatched', 'failed'].includes(String(value.status))
    || !['current', 'changed', 'expired', 'unknown'].includes(String(value.applicability))
    || value.error_code !== null && !['result_unknown', 'configuration_changed', 'connection_changed', 'invalid_receipt', 'result_unavailable', 'execution_failed', 'interrupted'].includes(String(value.error_code))
    || value.remaining_runs !== null && (typeof value.remaining_runs !== 'number' || !Number.isInteger(value.remaining_runs) || value.remaining_runs < 0 || value.remaining_runs > 100)
    || ![value.created_at, value.expires_at, value.dispatch_claimed_at].every(date => typeof date === 'string' && Number.isFinite(Date.parse(date)))
    || value.completed_at !== null && (typeof value.completed_at !== 'string' || !Number.isFinite(Date.parse(value.completed_at)))) return false;
  if (value.status === 'matched' || value.status === 'mismatched') {
    if (value.result_digest === null || value.completed_at === null || value.error_code !== null
      || (value.status === 'matched') !== (value.expected_digest === value.result_digest)) return false;
  }
  return value.status !== 'failed' || value.error_code !== null;
}

export function validScriptRequestState(value: unknown, project: string, actor: string, agent: string, pointer: ScriptTrialPointer): value is ScriptTrialRequestState {
  if (!object(value) || !exact(value, 'scope can_enable project_id actor_id agent_id request_id binding_id binding_revision status stop_status created_at deadline_at completed_at error_code report')
    || value.scope !== 'employee_device_script_trial_request' || value.can_enable !== false
    || value.project_id !== project || value.actor_id !== actor || value.agent_id !== agent
    || value.request_id !== pointer.requestId || value.binding_id !== pointer.bindingId || value.binding_revision !== pointer.bindingRevision
    || !['preparing', 'ready', 'failed', 'cancelled'].includes(String(value.status))
    || !['none', 'requested', 'confirmed', 'finished'].includes(String(value.stop_status))
    || value.error_code !== null && !['preparation_timeout', 'preparation_failed', 'interrupted', 'cancelled'].includes(String(value.error_code))
    || ![value.created_at, value.deadline_at].every(date => typeof date === 'string' && Number.isFinite(Date.parse(date)))
    || value.completed_at !== null && (typeof value.completed_at !== 'string' || !Number.isFinite(Date.parse(value.completed_at)))) return false;
  if (value.status === 'ready') {
    return value.completed_at !== null && value.error_code === null && validScriptReport(value.report, project, actor, agent, pointer);
  }
  if (value.report !== null) return false;
  if (value.status === 'preparing') return value.completed_at === null && value.error_code === null && value.stop_status === 'none';
  if (value.completed_at === null || value.error_code === null) return false;
  return value.status === 'cancelled' ? value.error_code === 'cancelled' && value.stop_status === 'confirmed'
    : value.error_code !== 'cancelled' && value.stop_status === 'none';
}

/** Convert the reviewed fields into business values, never commands or code. */
export function parseScriptFields(fields: ScriptParameter[], inputs: Record<string, string>, resultText = false): ScriptBusinessValues | null {
  const result: ScriptBusinessValues = {};
  if (!Array.isArray(fields) || fields.length > 32 || new Set(fields.map(field => field.name)).size !== fields.length) return null;
  for (const field of fields) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(field.name)) return null;
    const value = Object.prototype.hasOwnProperty.call(inputs, field.name) ? inputs[field.name] : '';
    if (typeof value !== 'string') return null;
    if (value === '' && !field.required) continue;
    if (field.kind === 'boolean') {
      if (!['true', 'false'].includes(value)) return null;
      result[field.name] = value === 'true';
    } else if (field.kind === 'integer') {
      const number = Number(value);
      if (!/^-?(0|[1-9]\d*)$/.test(value) || !Number.isSafeInteger(number)
        || field.minimum === null || field.maximum === null || number < field.minimum || number > field.maximum) return null;
      result[field.name] = number;
    } else if (field.kind === 'string') {
      if (field.required && value === '' || Array.from(value).length > field.max_length || /[\uD800-\uDFFF]/u.test(value)
        || (resultText ? /\u0000/.test(value) : /[\u0000-\u001f\u007f-\u009f\ufffd\u2028\u2029]/.test(value))
        || field.choices.length > 0 && !field.choices.includes(value)) return null;
      result[field.name] = value;
    } else return null;
  }
  const encoded = JSON.stringify(result).replace(/[<>&\u2028\u2029]/g, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return new TextEncoder().encode(encoded).length <= 65536 ? result : null;
}
