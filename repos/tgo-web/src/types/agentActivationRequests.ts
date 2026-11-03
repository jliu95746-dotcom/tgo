import type { AgentActivationSelection } from './agentActivationPolicies';

export interface ActivationPolicyExpectation extends AgentActivationSelection {
  policy_revision: number;
  requirements_revision: string;
}
export interface ActivationExpectation {
  checked_at: string;
  configuration_fingerprint: string;
  model_owner_revision: string;
  business_evidence_revision?: string | null;
  channels: ActivationPolicyExpectation[];
}
export interface AgentActivationRequest {
  request_key: string;
  expected: ActivationExpectation;
}
export type ActivationFailure = 'configuration_changed' | 'not_ready' | 'confirmation_expired' | 'permission_changed' | 'unavailable';
export interface AgentActivationReceipt {
  scope: 'employee_activation_request';
  can_enable: false;
  project_id: string;
  requested_by: string;
  agent_id: string;
  request_key: string;
  status: 'completed' | 'rejected' | 'not_found';
  command: AgentActivationRequest | null;
  finished_at: string | null;
  failure_code: ActivationFailure | null;
}
