import type { AgentActivationSelection } from './agentActivationPolicies';
import type { KnowledgeChannel } from './scenarioResources';
import type { KnowledgeCaseStatus } from './agentKnowledgeCapabilities';
import type { BusinessOperationCapability } from './agentBusinessTrials';

export interface ActivationBlocker {
  code: 'policy_missing' | 'requirements_changed' | 'owner_unavailable'
    | 'configuration_unavailable' | 'dependency_not_verified'
    | 'knowledge_not_ready' | 'tests_not_current';
  channel: KnowledgeChannel | null;
  resource: 'model' | 'knowledge' | 'connection' | 'tool' | 'workflow' | 'device' | null;
  message: string;
}

export interface ActivationChannelCoverage extends AgentActivationSelection {
  policy_revision: number;
  requirements_revision: string;
  status: KnowledgeCaseStatus;
  valid_until: string | null;
  business_capabilities: BusinessOperationCapability[];
}

export interface AgentActivationReadiness {
  project_id: string;
  agent_id: string;
  checked_by: string;
  checked_at: string;
  scope: 'employee_activation';
  enabled: boolean;
  can_enable: boolean;
  configuration_fingerprint: string | null;
  model_owner_revision: string | null;
  business_evidence_revision: string | null;
  blockers: ActivationBlocker[];
  channels: ActivationChannelCoverage[];
}
