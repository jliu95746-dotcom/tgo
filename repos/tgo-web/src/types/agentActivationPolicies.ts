import type { KnowledgeChannel } from './scenarioResources';

export interface AgentActivationSelection {
  template_id: 'knowledge' | 'aftersales' | 'logistics' | 'assistant';
  template_version: number;
  channel: KnowledgeChannel;
}

export interface AgentActivationPolicyChange {
  expected_revision: number;
  selection: AgentActivationSelection;
}

export interface AgentActivationPolicyRecord extends AgentActivationSelection {
  id: string;
  revision: number;
  requirements_revision: string;
  configured_by: string;
  enabled: boolean;
}

export interface AgentActivationPolicies {
  project_id: string;
  agent_id: string;
  policies: AgentActivationPolicyRecord[];
  can_enable: false;
}
