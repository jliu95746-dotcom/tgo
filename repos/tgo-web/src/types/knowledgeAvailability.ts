import type { KnowledgeChannel } from './knowledgeGovernance';

export interface CollectionAvailability {
  id: string;
  name: string;
  eligible_chunk_count: number;
  total_chunk_count: number;
  blocked_reasons: string[];
}

export interface AgentKnowledgeAvailability {
  project_id: string;
  channel: KnowledgeChannel;
  agent_id: string;
  binding_mode: 'project_default' | 'explicit' | 'unbound';
  collections: CollectionAvailability[];
  issues: string[];
}

export interface KnowledgeChannelUpdateRequest {
  channels: KnowledgeChannel[];
  expected_updated_at: string;
}
