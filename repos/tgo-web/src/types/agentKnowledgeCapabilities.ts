import type { KnowledgeReviewCase } from './agentTrialReviews';
import type { KnowledgeChannel } from './knowledgeGovernance';

export type KnowledgeCaseStatus = 'not_tested' | 'in_progress' | 'awaiting_review' | 'needs_retest' | 'failed' | 'passed' | 'unavailable';
export interface KnowledgeCaseCoverage {
  case: KnowledgeReviewCase;
  status: KnowledgeCaseStatus;
  report_id: string | null;
  review_id: string | null;
  review_revision: number | null;
  valid_until: string | null;
}
export interface KnowledgeCapabilityCoverage {
  template_id: 'knowledge' | 'aftersales';
  template_version: number;
  status: KnowledgeCaseStatus;
  answer_cases_passed: number;
  answer_cases_required: number;
  safety_cases_passed: number;
  safety_cases_required: number;
  cases: KnowledgeCaseCoverage[];
  valid_until: string | null;
}
export interface AgentKnowledgeCapabilities {
  agent_id: string;
  reviewed_by: string;
  channel: KnowledgeChannel;
  scope: 'knowledge_templates';
  can_enable: false;
  enabled: boolean;
  knowledge_status: 'available' | 'missing' | 'unknown';
  checked_at: string;
  capabilities: KnowledgeCapabilityCoverage[];
}
