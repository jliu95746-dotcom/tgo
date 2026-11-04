import { scriptUuid } from './agentDeviceScriptValidation';
import type { AgentScriptCaseRecord, AgentScriptCaseRead, AgentScriptCaseValues, AgentScriptEvidence,
  ScriptCaseSavePointer, ScriptCaseSaveState, ScriptRunCheck } from '@/types/agentDeviceScriptCases';
import type { ScriptBusinessValues } from '@/types/agentDeviceScripts';

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const exact = (value: Record<string, unknown>, names: string) => {
  const keys = names.split(' ');
  return Object.keys(value).length === keys.length && keys.every(key => Object.prototype.hasOwnProperty.call(value, key));
};
const digest = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const date = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));
const revision = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 2147483647;
export function validCaseSaveState(value: unknown, project: string, actor: string, agent: string,
  pointer: ScriptCaseSavePointer): value is ScriptCaseSaveState {
  if (!record(value) || !exact(value, 'scope can_enable project_id actor_id agent_id request_id binding_id binding_revision case_id expected_revision status case created_at deadline_at completed_at error_code')
    || value.scope !== 'employee_device_script_case_save' || value.can_enable !== false
    || value.project_id !== project || value.actor_id !== actor || value.agent_id !== agent
    || value.request_id !== pointer.requestId || value.binding_id !== pointer.bindingId || value.binding_revision !== pointer.bindingRevision
    || value.case_id !== pointer.caseId || value.expected_revision !== pointer.expectedRevision
    || !date(value.created_at) || !date(value.deadline_at)) return false;
  if (value.status === 'preparing') return value.case === null && value.completed_at === null && value.error_code === null;
  if (!date(value.completed_at)) return false;
  if (value.status === 'saved') return value.error_code === null && validScriptCase(value.case, project, agent)
    && value.case.created_by === actor && value.case.request_id === pointer.requestId
    && value.case.binding_id === pointer.bindingId && value.case.binding_revision === pointer.bindingRevision
    && value.case.revision === pointer.expectedRevision + 1 && (pointer.caseId === null || value.case.case_id === pointer.caseId)
    && value.case.device_id === pointer.deviceId && value.case.policy_digest === pointer.policyDigest && value.case.grant_digest === pointer.grantDigest;
  return value.case === null && (value.status === 'cancelled' && value.error_code === 'cancelled'
    || value.status === 'failed' && ['preparation_failed', 'preparation_expired'].includes(String(value.error_code)));
}
function businessValues(value: unknown, maximum: number, required = false): value is ScriptBusinessValues {
  if (!record(value) || Object.keys(value).length > maximum || required && Object.keys(value).length === 0) return false;
  if (!Object.entries(value).every(([key, field]) => /^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(key)
    && (typeof field === 'boolean' || typeof field === 'number' && Number.isSafeInteger(field)
      || typeof field === 'string' && !/[\uD800-\uDFFF]/u.test(field)))) return false;
  return new TextEncoder().encode(JSON.stringify(value)).length <= 65536;
}
export function validScriptCase(value: unknown, project: string, agent: string): value is AgentScriptCaseRecord {
  return record(value) && exact(value, 'scope can_enable id case_id project_id agent_id created_by request_id revision title binding_id binding_revision device_id policy_digest grant_digest case_digest status created_at expires_at')
    && value.scope === 'employee_device_script_case' && value.can_enable === false
    && value.project_id === project && value.agent_id === agent
    && [value.id, value.case_id, value.created_by, value.request_id, value.binding_id, value.device_id].every(scriptUuid)
    && revision(value.revision) && revision(value.binding_revision)
    && typeof value.title === 'string' && Boolean(value.title.trim()) && Array.from(value.title).length <= 80
    && !/[\u0000-\u001f]/.test(value.title)
    && [value.policy_digest, value.grant_digest, value.case_digest].every(digest)
    && ['current', 'changed', 'superseded', 'expired'].includes(String(value.status))
    && date(value.created_at) && date(value.expires_at);
}
export function validScriptCaseValues(value: unknown): value is AgentScriptCaseValues {
  return record(value) && exact(value, 'parameters expected_fields')
    && businessValues(value.parameters, 16) && businessValues(value.expected_fields, 32, true);
}
export function validScriptCaseRead(value: unknown, project: string, agent: string, version: string): value is AgentScriptCaseRead {
  return record(value) && exact(value, 'case values') && validScriptCase(value.case, project, agent)
    && value.case.id === version && validScriptCaseValues(value.values);
}
export function validScriptEvidence(value: unknown, project: string, actor: string, agent: string, report: string): value is AgentScriptEvidence {
  if (!record(value) || !exact(value, 'scope can_enable project_id actor_id agent_id report_id status case values result_fields captured_at expires_at')
    || value.scope !== 'employee_device_script_evidence' || value.can_enable !== false
    || value.project_id !== project || value.actor_id !== actor || value.agent_id !== agent || value.report_id !== report
    || !['available', 'pending', 'unavailable', 'expired', 'not_retained'].includes(String(value.status))
    || value.case !== null && !validScriptCase(value.case, project, agent)
    || value.captured_at !== null && !date(value.captured_at) || value.expires_at !== null && !date(value.expires_at)) return false;
  return value.status === 'available'
    ? value.case !== null && validScriptCaseValues(value.values) && businessValues(value.result_fields, 32)
      && date(value.captured_at) && date(value.expires_at)
    : value.values === null && value.result_fields === null;
}
export function validCaseSavePointer(value: unknown): value is ScriptCaseSavePointer {
  return record(value) && exact(value, 'requestId caseId expectedRevision bindingId bindingRevision deviceId policyDigest grantDigest')
    && [value.requestId, value.bindingId, value.deviceId].every(scriptUuid)
    && typeof value.expectedRevision === 'number' && Number.isInteger(value.expectedRevision)
    && value.expectedRevision >= 0 && value.expectedRevision < 2147483647
    && (value.caseId === null ? value.expectedRevision === 0 : scriptUuid(value.caseId) && value.expectedRevision > 0)
    && revision(value.bindingRevision) && digest(value.policyDigest) && digest(value.grantDigest);
}

export function validScriptRunCheck(value: unknown, project: string, actor: string, agent: string, report: string): value is ScriptRunCheck {
  if (!record(value) || !exact(value, 'scope can_enable review status remaining_runs checked_at valid_until')
    || value.scope !== 'employee_device_script_run_check' || value.can_enable !== false
    || !date(value.checked_at) || !record(value.review)
    || !exact(value.review, 'scope can_enable project_id actor_id agent_id report_id status review')) return false;
  const review = value.review;
  if (review.scope !== 'employee_device_script_review' || review.can_enable !== false
    || review.project_id !== project || review.actor_id !== actor || review.agent_id !== agent || review.report_id !== report
    || !['unreviewed', 'approved', 'rejected', 'revoked', 'expired', 'changed'].includes(String(review.status))) return false;
  const row = review.review;
  if (row === null) {
    if (review.status !== 'unreviewed') return false;
  } else {
    if (!record(row) || !exact(row, 'id case_version_id case_digest result_digest decision reviewed_at expires_at revoked_at')
      || !scriptUuid(row.id) || !scriptUuid(row.case_version_id) || !digest(row.case_digest) || !digest(row.result_digest)
      || !['approved', 'rejected'].includes(String(row.decision)) || !date(row.reviewed_at) || !date(row.expires_at)
      || Date.parse(row.expires_at) <= Date.parse(row.reviewed_at)) return false;
    if (row.revoked_at !== null) {
      if (!date(row.revoked_at) || Date.parse(row.revoked_at) < Date.parse(row.reviewed_at)
        || row.decision !== 'approved' || review.status !== 'revoked') return false;
    } else if (row.decision === 'rejected' ? review.status !== 'rejected'
      : !['approved', 'expired', 'changed'].includes(String(review.status))) return false;
  }
  const age = Date.now() - Date.parse(value.checked_at);
  if (age < -5000 || age > 30000
    || !['ready', 'review_required', 'device_changed', 'budget_exhausted', 'unavailable'].includes(String(value.status))) return false;
  if (value.status === 'ready' || value.status === 'budget_exhausted') {
    if (review.status !== 'approved' || !record(row) || !date(row.expires_at)
      || typeof value.remaining_runs !== 'number' || !Number.isInteger(value.remaining_runs)
      || value.remaining_runs < 0 || value.remaining_runs > 100) return false;
    if (value.status === 'budget_exhausted') return value.remaining_runs === 0 && value.valid_until === null;
    return value.remaining_runs > 0 && date(value.valid_until)
      && Date.parse(value.valid_until) > Math.max(Date.now(), Date.parse(value.checked_at))
      && Date.parse(value.valid_until) <= Math.min(Date.parse(row.expires_at), Date.parse(value.checked_at) + 30000);
  }
  return value.remaining_runs === null && value.valid_until === null
    && (value.status !== 'review_required' || review.status !== 'approved');
}
