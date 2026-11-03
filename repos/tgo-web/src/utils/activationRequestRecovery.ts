import type { AgentActivationRequest, AgentActivationReceipt } from '@/types/agentActivationRequests';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const hash = /^[0-9a-f]{64}$/;
const channels = ['web', 'wecom_kf', 'app', 'phone', 'internal'];
const roles = ['knowledge', 'aftersales', 'logistics', 'assistant'];
const date = (value: unknown): value is string => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const keys = (value: Record<string, unknown>, expected: string[]) => Object.keys(value).length === expected.length && expected.every(key => key in value);
const integer = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value > 0;

export function validActivationRequest(value: unknown): value is AgentActivationRequest {
  if (!object(value) || !keys(value, ['request_key', 'expected']) || typeof value.request_key !== 'string' || !uuid.test(value.request_key) || !object(value.expected)) return false;
  const expected = value.expected;
  const fields = ['checked_at', 'configuration_fingerprint', 'model_owner_revision', 'channels'];
  const revision = expected.business_evidence_revision;
  return (keys(expected, fields) || keys(expected, [...fields, 'business_evidence_revision']))
    && (revision === undefined || revision === null || typeof revision === 'string' && hash.test(revision))
    && date(expected.checked_at) && typeof expected.configuration_fingerprint === 'string' && hash.test(expected.configuration_fingerprint)
    && typeof expected.model_owner_revision === 'string' && hash.test(expected.model_owner_revision)
    && Array.isArray(expected.channels) && expected.channels.length > 0 && expected.channels.length <= 5
    && expected.channels.every(row => object(row) && keys(row, ['channel', 'template_id', 'template_version', 'policy_revision', 'requirements_revision'])
      && typeof row.channel === 'string' && channels.includes(row.channel) && typeof row.template_id === 'string' && roles.includes(row.template_id)
      && integer(row.template_version) && integer(row.policy_revision) && typeof row.requirements_revision === 'string' && hash.test(row.requirements_revision))
    && new Set(expected.channels.map(row => row.channel)).size === expected.channels.length;
}
function canonical(command: AgentActivationRequest): string {
  return JSON.stringify([command.request_key, Date.parse(command.expected.checked_at), command.expected.configuration_fingerprint,
    command.expected.model_owner_revision, command.expected.business_evidence_revision ?? null,
    command.expected.channels.map(row => [row.channel, row.template_id, row.template_version, row.policy_revision, row.requirements_revision]).sort()]);
}
export function validActivationReceipt(receipt: AgentActivationReceipt, command: AgentActivationRequest, project: string, actor: string, agent: string): boolean {
  if (!receipt || receipt.scope !== 'employee_activation_request' || receipt.can_enable !== false || receipt.project_id !== project
    || receipt.requested_by !== actor || receipt.agent_id !== agent || receipt.request_key !== command.request_key) return false;
  if (receipt.status === 'not_found') return receipt.command === null && receipt.finished_at === null && receipt.failure_code === null;
  return ['completed', 'rejected'].includes(receipt.status) && validActivationRequest(receipt.command)
    && canonical(receipt.command) === canonical(command) && date(receipt.finished_at)
    && (receipt.status === 'completed' ? receipt.failure_code === null
      : ['configuration_changed', 'not_ready', 'confirmation_expired', 'permission_changed', 'unavailable'].includes(receipt.failure_code ?? ''));
}
const storageKey = (scope: string, agent: string) => `tgo.activation-request.v1:${scope}:${agent}`;
export function rememberActivation(scope: string, agent: string, command: AgentActivationRequest): void {
  if (!validActivationRequest(command)) throw new Error('invalidActivationIntent');
  localStorage.setItem(storageKey(scope, agent), JSON.stringify(command));
}
export function rememberedActivation(scope: string, agent: string): AgentActivationRequest | null {
  const raw = localStorage.getItem(storageKey(scope, agent));
  if (!raw) return null;
  const command: unknown = JSON.parse(raw);
  if (!validActivationRequest(command)) throw new Error('invalidStoredActivationIntent');
  return command;
}
export function forgetActivation(scope: string, agent: string): void {
  localStorage.removeItem(storageKey(scope, agent));
}
