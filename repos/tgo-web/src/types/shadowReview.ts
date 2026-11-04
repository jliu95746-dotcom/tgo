import type { IntentName, IntentRoute, RiskLevel } from './messageAnalysis';

export interface AssistRunTrace {
  phase: 'fact_answer' | 'expression_rewrite' | 'fact_audit';
  model_name: string | null;
  configured_prompt_sha256: string | null;
  api_template_sha256?: string | null;
  requested_agent_id?: string | null;
  request_system_prompt_sha256?: string | null;
}

export interface ShadowStaffReply {
  client_msg_no: string;
  content: string;
  ai_draft: string | null;
  ai_draft_id: string | null;
  source_verified: boolean;
  generation_trace?: AssistRunTrace[];
  review: ShadowReviewState | null;
  created_at: string;
}

export type ShadowVerdict = 'acceptable' | 'minor_issue' | 'severe_error' | 'insufficient_evidence';
export type ShadowReason = 'wrong_fact' | 'privacy' | 'unsafe_action' | 'missing_handoff' | 'other';
export type ShadowAdjudicationVerdict = Exclude<ShadowVerdict, 'insufficient_evidence'>;

export interface ShadowAdjudicationRequest {
  request_id: string;
  batch_id: string;
  draft_id: string;
  visitor_id: string;
  expected_review_sha256: string;
  verdict: ShadowAdjudicationVerdict;
  reason_code: ShadowReason | null;
  note: string;
}

export interface ShadowAdjudicationReceipt {
  id: string;
  batch_id: string;
  draft_id: string;
  adjudicator_id: string;
  request_id: string;
  review_set_sha256: string;
  verdict: ShadowAdjudicationVerdict;
  reason_code: ShadowReason | null;
  note: string;
  created_at: string;
}

export interface ShadowAdjudicationHistory {
  items: ShadowAdjudicationReceipt[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface ShadowReviewState {
  verdict: ShadowVerdict;
  reason_code: ShadowReason | null;
  reviewed_at: string;
}

export interface ShadowReviewRequest {
  draft_id: string;
  visitor_id: string;
  request_id: string;
  verdict: ShadowVerdict;
  reason_code: ShadowReason | null;
  note: string | null;
}

export interface ShadowReviewReceipt {
  id: string;
  draft_id: string;
  reviewer_id: string | null;
  verdict: ShadowVerdict;
  reason_code: ShadowReason | null;
  note: string | null;
  created_at: string;
}

export interface ShadowReviewSummary {
  window_start: string;
  window_end: string;
  eligible_drafts: number;
  evaluated_drafts: number;
  insufficient_drafts: number;
  unreviewed_drafts: number;
  ever_severe_drafts: number;
  current_severe_drafts: number;
  verified_eligible_drafts: number;
  verified_evaluated_drafts: number;
  verified_ever_severe_drafts: number;
}

export interface ShadowPendingReviewItem {
  draft_id: string;
  visitor_id: string;
  created_at: string;
}

export interface ShadowPendingReviewQueue {
  items: ShadowPendingReviewItem[];
  total_pending: number;
  next_cursor: string | null;
}

export interface ShadowSampleCreateRequest {
  request_id: string;
  days: number;
  sample_size: number;
}

export interface ShadowSampleItem {
  position: number;
  draft_id: string;
  visitor_id: string;
  reviewer_count: number;
  review_status: 'unreviewed' | 'needs_second' | 'agreed' | 'adjudicated' | 'disputed' | 'insufficient' | 'severe_flagged' | 'missing';
  review_set_sha256: string | null;
  adjudication_verdict: ShadowAdjudicationVerdict | null;
}

export interface ShadowSampleBatch {
  id: string;
  request_id: string;
  created_at: string;
  window_start: string;
  window_end: string;
  algorithm: string;
  random_seed: string;
  population_sha256: string;
  population_count: number;
  requested_size: number;
  sample_count: number;
  reviewed_count: number;
  insufficient_count: number;
  unreviewed_count: number;
  current_severe_count: number;
  ever_severe_count: number;
  missing_drafts_count: number;
  ready_count: number;
  resolved_count: number;
  needs_second_count: number;
  disputed_count: number;
  insufficient_case_count: number;
  severe_flagged_count: number;
  items: ShadowSampleItem[];
}

export interface ShadowSampleBatchSummary {
  id: string;
  created_at: string;
  window_start: string;
  window_end: string;
  population_count: number;
  requested_size: number;
  sample_count: number;
}

export interface ShadowSampleBatchList {
  items: ShadowSampleBatchSummary[];
  has_more: boolean;
}

export interface ShadowProtocolCreateRequest {
  request_id: string;
  window_start: string;
  window_end: string;
  sample_size: number;
  target_channel: 'wecom_kf';
}

export interface ShadowReviewProtocol {
  id: string;
  request_id: string;
  created_at: string;
  window_start: string;
  window_end: string;
  sample_size: number;
  algorithm: string;
  target_channel: 'wecom_kf' | 'all';
  seed_sha256: string;
  status: 'scheduled' | 'frozen' | 'empty';
  finalized_at: string | null;
  batch_id: string | null;
  population_count: number | null;
  population_sha256: string | null;
  manifest_sha256: string | null;
}

export interface ShadowReviewProtocolList {
  items: ShadowReviewProtocol[];
  has_more: boolean;
}

export interface ShadowProtocolCandidateItem {
  draft_id: string;
  visitor_id: string;
  selected_position: number | null;
}

export interface ShadowProtocolCandidatePage {
  protocol_id: string;
  total_candidates: number;
  population_sha256: string;
  items: ShadowProtocolCandidateItem[];
  next_cursor: string | null;
}

export interface ShadowProtocolIntegrity {
  protocol_id: string;
  consistent: boolean;
  population_valid: boolean;
  manifest_valid: boolean;
  sample_valid: boolean;
  metadata_valid: boolean;
  population_count: number;
  recorded_population_count: number | null;
  population_sha256: string;
  recorded_population_sha256: string | null;
  manifest_sha256: string;
  recorded_manifest_sha256: string | null;
}

export type ShadowGateBlocker =
  | 'window_not_frozen'
  | 'empty_sample'
  | 'manifest_inconsistent'
  | 'severe_history'
  | 'evidence_missing'
  | 'review_incomplete'
  | 'official_channel_required'
  | 'independent_shadow_missing';

export interface ShadowGateAssessment {
  protocol_id: string;
  batch_id: string | null;
  status: 'blocked' | 'review_complete';
  blockers: ShadowGateBlocker[];
  can_request_g4_signoff: boolean;
  requires_live_channel_verification: true;
  integrity_consistent: boolean | null;
  sample_count: number | null;
  ready_count: number | null;
  historical_severe_count: number | null;
  assessed_at: string;
}

export interface ShadowGateSignoffRequest {
  request_id: string;
  evidence_reference: string;
  live_case_count: number;
  note: string;
}

export interface ShadowGateDecisionRequest {
  request_id: string;
  decision: 'accepted' | 'rejected';
  note: string;
  live_evidence_verified: boolean;
}

export interface ShadowGateSignoffReceipt {
  id: string;
  protocol_id: string;
  batch_id: string;
  requester_id: string;
  request_id: string;
  manifest_sha256: string;
  review_evidence_sha256: string;
  sample_count: number;
  evidence_reference: string;
  live_case_count: number;
  note: string;
  created_at: string;
  status: 'pending' | 'accepted' | 'rejected';
  decider_id: string | null;
  decision_request_id: string | null;
  decision_note: string | null;
  decided_at: string | null;
}

export interface ShadowComparison {
  source_message_id: string;
  intent: IntentName;
  confidence: number;
  risk_level: RiskLevel;
  recommended_route: IntentRoute;
  need_human: boolean;
  created_at: string;
  replies: ShadowStaffReply[];
}

export interface ShadowComparisonsResponse {
  items: ShadowComparison[];
  has_more: boolean;
  reply_limit_reached: boolean;
}
