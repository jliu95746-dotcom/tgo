import type { AgentTrialReport } from './agentTrials';

export interface KnowledgeReviewCase {
  id: string;
  template_id: 'knowledge' | 'aftersales';
  template_version: number;
  criteria_version: 1;
  question: string;
  expected_result: 'grounded_answer' | 'safe_refusal';
  valid_from?: string | null;
}
export interface KnowledgeCatalog {
  agent_id: string;
  id: string | null;
  revision: number;
  request_key: string | null;
  configured_by: string | null;
  created_at: string | null;
  current: boolean;
  cases: KnowledgeReviewCase[];
}
export interface KnowledgeCatalogWrite {
  request_key: string;
  expected_revision: number;
  questions: string[];
}
export interface KnowledgeReviewChecks {
  answer_correct: boolean;
  evidence_checked: boolean;
  no_unverified_claims: boolean;
  expected_behavior: boolean;
}
export interface KnowledgeReviewRequest {
  request_key: string;
  case_id: string;
  decision: 'passed' | 'rejected';
  checks: KnowledgeReviewChecks;
  note: string;
  expected_latest_review_id: string | null;
}
export interface KnowledgeReviewResponse {
  id: string;
  revision: number;
  agent_id: string;
  report_id: string;
  request_key: string;
  reviewed_by: string;
  scope: 'knowledge_case';
  can_enable: false;
  decision: 'passed' | 'rejected';
  checks: KnowledgeReviewChecks;
  note: string;
  case: KnowledgeReviewCase;
  case_status: 'current' | 'changed';
  applicability: NonNullable<AgentTrialReport['applicability']>;
  superseded: boolean;
  effective: boolean;
  created_at: string;
  valid_until: string;
}
export interface KnowledgeReviewState {
  agent_id: string;
  report_id: string;
  scope: 'knowledge_case';
  can_enable: false;
  available_cases: KnowledgeReviewCase[];
  applicability: NonNullable<AgentTrialReport['applicability']>;
  can_review: boolean;
  valid_until: string;
  reviews: KnowledgeReviewResponse[];
}
