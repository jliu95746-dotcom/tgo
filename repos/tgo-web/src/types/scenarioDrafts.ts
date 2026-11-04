import type { ScenarioId, ScenarioModule } from './businessScenarios';
import type { KnowledgeChannel, ResourceKind, ScenarioBindings } from './scenarioResources';
import type { AgentActivationSelection } from './agentActivationPolicies';

export type ScenarioStep = 'scenario' | 'business' | 'resources' | 'testing' | 'activation';
export interface ScenarioBusiness {
  name: string;
  profession: string;
  company_summary: string;
  instructions: string;
}
export interface ScenarioAgentSettings {
  markdown: boolean;
  add_datetime_to_context: boolean;
  skills_enabled: boolean;
  show_tool_calls: boolean;
  tool_call_limit: number;
  num_history_runs: number;
  humanization_skill_name: string | null;
  humanization_skill_enabled: boolean;
}
export interface ScenarioTemplate {
  id: ScenarioId;
  module: ScenarioModule;
  version: number;
  title: string;
  available: boolean;
  default_model_mode: 'system_default' | 'not_required';
  required_resources: ResourceKind[];
  required_capabilities: string[];
  business_defaults: ScenarioBusiness;
  test_questions: string[];
  recommended_workflows: string[];
}
export interface ScenarioDraftCreate {
  idempotency_key: string;
  template_id: ScenarioId;
  module: ScenarioModule;
  template_version: number;
  channel: KnowledgeChannel;
  business: ScenarioBusiness;
  bindings: ScenarioBindings;
  agent_settings: ScenarioAgentSettings;
}
export interface ScenarioDraftUpdate {
  expected_revision: number;
  step?: ScenarioStep;
  channel?: KnowledgeChannel;
  business?: Partial<ScenarioBusiness>;
  bindings?: ScenarioBindings;
  agent_settings?: Partial<ScenarioAgentSettings>;
}
export interface ScenarioDraft {
  id: string;
  project_id: string;
  created_by: string;
  template_id: ScenarioId;
  module: ScenarioModule;
  template_version: number;
  channel: KnowledgeChannel;
  revision: number;
  step: ScenarioStep;
  status: 'draft';
  business: ScenarioBusiness;
  bindings: ScenarioBindings;
  agent_settings: ScenarioAgentSettings;
  created_at: string;
  updated_at: string;
}
export type ScenarioDraftInput = Omit<ScenarioDraftCreate, 'idempotency_key'> & { step: ScenarioStep };

export interface ScenarioAgentReceipt {
  project_id: string;
  created_by: string;
  draft_id: string;
  revision: number;
  agent_id: string;
  is_active: boolean;
  can_enable: false;
  /** Older servers may omit metadata; absence never proves readiness. */
  activation_selection?: AgentActivationSelection | null;
}
