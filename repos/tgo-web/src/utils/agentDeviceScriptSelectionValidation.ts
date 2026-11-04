import { scriptUuid } from './agentDeviceScriptValidation';
import type { ScriptSelectionRecord, ScriptSelectionState, ScriptSelectionWrite } from '@/types/agentDeviceScriptSelection';

const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const keys = (value: Record<string, unknown>, names: string) => Object.keys(value).sort().join() === names.split(',').sort().join();
const revision = (value: unknown, minimum = 1): value is number => typeof value === 'number' && Number.isInteger(value) && value >= minimum && value <= 2147483647;
const time = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));

export function validSelectionWrite(value: unknown): value is ScriptSelectionWrite {
  return object(value) && keys(value, 'request_id,expected_revision,enabled,report_id,review_id')
    && scriptUuid(value.request_id) && revision(value.expected_revision, 0) && value.expected_revision < 2147483647
    && typeof value.enabled === 'boolean' && (value.enabled ? scriptUuid(value.report_id) && scriptUuid(value.review_id)
      : value.report_id === null && value.review_id === null);
}

export function validSelectionRecord(value: unknown, project: string, agent: string): value is ScriptSelectionRecord {
  return object(value) && keys(value, 'scope,can_enable,id,project_id,agent_id,request_id,configured_by,revision,enabled,binding_id,binding_revision,report_id,review_id,saved_at,expires_at,selection_digest')
    && value.scope === 'employee_device_script_selection' && value.can_enable === false
    && value.project_id === project && value.agent_id === agent
    && [value.id, value.project_id, value.agent_id, value.request_id, value.configured_by, value.binding_id, value.report_id, value.review_id].every(scriptUuid)
    && revision(value.revision) && revision(value.binding_revision) && typeof value.enabled === 'boolean'
    && time(value.saved_at) && time(value.expires_at) && (!value.enabled || Date.parse(value.expires_at) > Date.parse(value.saved_at))
    && typeof value.selection_digest === 'string' && /^[0-9a-f]{64}$/.test(value.selection_digest);
}

export function validSelectionState(value: unknown, project: string, actor: string, agent: string): value is ScriptSelectionState {
  if (!object(value) || !keys(value, 'scope,can_enable,project_id,actor_id,agent_id,selection,status')
    || value.scope !== 'employee_device_script_selection_state' || value.can_enable !== false
    || value.project_id !== project || value.actor_id !== actor || value.agent_id !== agent
    || !['unselected', 'selected', 'changed', 'expired', 'revoked'].includes(String(value.status))) return false;
  const selected = value.selection;
  if (selected !== null && !validSelectionRecord(selected, project, agent)) return false;
  if ((!selected || !selected.enabled) !== (value.status === 'unselected')) return false;
  return value.status !== 'selected' || Boolean(selected && selected.configured_by === actor);
}
