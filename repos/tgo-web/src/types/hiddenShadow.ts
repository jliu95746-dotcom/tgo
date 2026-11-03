import type { ShadowReason, ShadowVerdict } from './shadowReview';

export interface HiddenShadowProtocol {
  id: string;
  request_id: string;
  created_at: string;
  window_start: string;
  window_end: string;
  sample_size: number;
  algorithm: string;
  seed_sha256: string | null;
  random_seed: string | null;
  status: 'scheduled' | 'frozen' | 'empty' | 'cancelled';
  finalized_at: string | null;
  cancelled_at: string | null;
  cancelled_by: string | null;
  population_count: number | null;
  sample_count: number | null;
  population_sha256: string | null;
  manifest_sha256: string | null;
}

export interface HiddenShadowProtocolList {
  items: HiddenShadowProtocol[];
  has_more: boolean;
}

export interface HiddenShadowProtocolCreateRequest {
  request_id: string;
  window_start: string;
  window_end: string;
  sample_size: number;
}

export interface HiddenShadowCasePage {
  protocol_id: string;
  total_cases: number;
  items: Array<{
    candidate_id: string;
    visitor_id: string;
    selected_position: number | null;
  }>;
  next_cursor: string | null;
}

export interface HiddenShadowIntegrity {
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
  manifest_sha256: string | null;
  recorded_manifest_sha256: string | null;
}

export interface HiddenShadowCaseDetail {
  protocol_id: string;
  candidate_id: string;
  visitor_id: string;
  selected_position: number;
  source_message_id: string;
  source_content_sha256: string;
  answer: string;
  staff_reply: string;
  evidence_sha256: string;
}

export interface HiddenShadowSourceMessage {
  protocol_id: string;
  candidate_id: string;
  source_message_id: string;
  content: string;
  content_sha256: string;
  message_seq: number;
  verified_at: string;
}

export interface HiddenShadowReviewRequest {
  request_id: string;
  verdict: ShadowVerdict;
  reason_code: ShadowReason | null;
  note: string | null;
}

export interface HiddenShadowReviewReceipt {
  id: string;
  protocol_id: string;
  candidate_id: string;
  reviewer_id: string;
  evidence_sha256: string;
  source_verified_at: string | null;
  source_message_seq: number | null;
  source_content_sha256: string | null;
  verdict: ShadowVerdict;
  reason_code: ShadowReason | null;
  note: string | null;
  created_at: string;
}

export interface HiddenShadowReviewSummary {
  protocol_id: string;
  selected_cases: number;
  completed_cases: number;
  review_complete: boolean;
  severe_cases: number | null;
  disputed_cases: number | null;
  insufficient_cases: number | null;
}

export type HiddenShadowAdjudicationVerdict = Exclude<ShadowVerdict, 'insufficient_evidence'>;

export interface HiddenShadowAdjudicationRequest {
  request_id: string;
  expected_review_sha256: string;
  verdict: HiddenShadowAdjudicationVerdict;
  reason_code: ShadowReason | null;
  note: string;
}

export interface HiddenShadowAdjudicationReceipt {
  id: string;
  protocol_id: string;
  candidate_id: string;
  adjudicator_id: string;
  request_id: string;
  review_set_sha256: string;
  evidence_sha256: string;
  verdict: HiddenShadowAdjudicationVerdict;
  reason_code: ShadowReason | null;
  note: string;
  created_at: string;
}

export interface HiddenShadowReviewPair {
  protocol_id: string;
  candidate_id: string;
  visitor_id: string;
  evidence_sha256: string;
  review_set_sha256: string;
  reviews: [HiddenShadowReviewReceipt, HiddenShadowReviewReceipt];
  adjudication: HiddenShadowAdjudicationReceipt | null;
}

export type HiddenShadowGateBlocker =
  | 'window_not_frozen'
  | 'empty_sample'
  | 'manifest_inconsistent'
  | 'review_incomplete'
  | 'evidence_changed'
  | 'evidence_missing'
  | 'dispute_unresolved'
  | 'severe_history';

export interface HiddenShadowGateAssessment {
  protocol_id: string;
  status: 'blocked' | 'review_complete';
  blockers: HiddenShadowGateBlocker[];
  can_request_g4_signoff: boolean;
  requires_live_channel_verification: true;
  integrity_consistent: boolean | null;
  sample_count: number | null;
  completed_cases: number | null;
  severe_cases: number | null;
  insufficient_cases: number | null;
  disputed_cases: number | null;
  adjudicated_cases: number | null;
  unresolved_disputes: number | null;
  review_evidence_sha256: string | null;
  assessed_at: string;
}

export interface HiddenShadowGateSignoffRequest {
  request_id: string;
  evidence_reference: string;
  live_case_count: number;
  note: string;
}

export interface HiddenShadowGateDecisionRequest {
  request_id: string;
  decision: 'accepted' | 'rejected';
  note: string;
  live_evidence_verified: boolean;
}

export interface HiddenShadowGateSignoffReceipt {
  id: string;
  protocol_id: string;
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
  current_evidence_valid: boolean | null;
  decider_id: string | null;
  decision_request_id: string | null;
  decision_note: string | null;
  decided_at: string | null;
}
