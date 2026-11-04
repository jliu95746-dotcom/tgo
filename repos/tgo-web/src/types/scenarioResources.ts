export type ResourceKind = 'model' | 'knowledge' | 'tool' | 'workflow' | 'connection' | 'device';
export type ResourceStatus = 'configured' | 'not_ready' | 'offline' | 'disabled' | 'unknown';
export type KnowledgeChannel = 'web' | 'wecom_kf' | 'app' | 'phone' | 'internal';

export interface ScenarioResource {
  id: string;
  kind: ResourceKind;
  name: string;
  status: ResourceStatus;
  details: {
    provider_id: string | null;
    model_id: string | null;
    purpose: string | null;
    is_default: boolean;
    eligible_chunk_count: number | null;
    total_chunk_count: number | null;
    blocked_reasons: string[];
    workflow_version: number | null;
    resource_revision?: string | null;
  };
}

export interface ResourceSection {
  kind: ResourceKind;
  status: 'available' | 'partial' | 'unavailable' | 'not_supported';
  items: ScenarioResource[];
  message: string | null;
}

export interface ScenarioResourceCatalog {
  project_id: string;
  channel: KnowledgeChannel;
  checked_at: string;
  sections: ResourceSection[];
}

export interface ScenarioBindings {
  model: { provider_id: string; model_id: string } | null;
  knowledge_ids: string[];
  tool_ids: string[];
  workflow_ids: string[];
  connection_ids: string[];
  device_ids: string[];
}

export interface ScenarioReadinessRequest {
  template_id: import('./businessScenarios').ScenarioId;
  channel: KnowledgeChannel;
  bindings: ScenarioBindings;
}

export interface ScenarioReadinessReport {
  project_id: string;
  template_id: import('./businessScenarios').ScenarioId;
  template_version: number;
  channel: KnowledgeChannel;
  configuration_ready: boolean;
  test_status: 'not_tested';
  can_enable: false;
  effective_bindings: ScenarioBindings;
  issues: { kind: ResourceKind; code: string; message: string; action: 'retry' | 'choose_resource' | 'configure_resource'; route: string }[];
}
