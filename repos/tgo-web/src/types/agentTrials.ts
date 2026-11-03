import type { KnowledgeChannel } from './scenarioResources';

export interface AgentTrialRequest {
  request_key: string;
  message: string;
  channel: KnowledgeChannel;
  timeout: number;
}

export interface TrialKnowledgeSource {
  collection_id: string;
  document_id: string;
  file_id: string | null;
  content_digest: string;
  relevance_score: number;
}

export interface AgentTrialReport {
  id: string;
  agent_id: string;
  request_key: string;
  mode: 'knowledge_grounded';
  status: 'running' | 'completed' | 'blocked' | 'failed' | 'cancelled';
  assessment: 'not_reviewed';
  can_enable: false;
  channel: KnowledgeChannel;
  question: string;
  answer: string;
  configuration_fingerprint: string | null;
  knowledge_snapshot?: {
    version: 1;
    channel: KnowledgeChannel;
    binding_mode: 'project_default' | 'explicit' | 'unbound';
    collections: { collection_id: string; resource_revision: string | null }[];
    issues: string[];
    captured_at: string;
    verification: 'pending' | 'consistent' | 'changed' | 'unknown';
  } | null;
  applicability?: {
    scope: 'agent_configuration_and_knowledge';
    status: 'not_recorded' | 'not_checked' | 'current' | 'changed' | 'unknown';
    checked_at: string | null;
    issues: ('trial_not_completed' | 'snapshot_not_recorded' | 'changed_during_trial'
      | 'configuration_changed' | 'knowledge_changed' | 'knowledge_unavailable'
      | 'agent_unavailable' | 'revision_unconfirmed')[];
  };
  knowledge_status: 'matched' | 'no_match' | 'unavailable' | 'over_budget' | 'skipped' | null;
  sources: TrialKnowledgeSource[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}
