/**
 * Employee-console projection of the persisted message-analysis contract.
 *
 * The write/read API currently exposed by tgo-api is authenticated with a
 * platform integration key. That credential must never be sent to a browser,
 * so these types intentionally contain no request headers or client API shape.
 */

export type MediaAnalysisType = 'voice' | 'image';

export type MediaAnalysisStatus = 'completed' | 'partial' | 'failed';

export type AnalysisCapability = 'asr' | 'ocr' | 'vlm';

export type AnalysisStageStatus = 'completed' | 'failed';

export type AnalysisErrorCategory =
  | 'timeout'
  | 'invalid_media'
  | 'provider_not_configured'
  | 'provider_failure';

export type SensitiveDataCategory =
  | 'phone_number'
  | 'identity_number'
  | 'payment_account'
  | 'email'
  | 'address';

export interface AnalysisStageError {
  category: AnalysisErrorCategory;
  message: string;
  retryable: boolean;
}

export interface AnalysisStageResult {
  capability: AnalysisCapability;
  status: AnalysisStageStatus;
  provider_name: string | null;
  text: string | null;
  confidence: number | null;
  model_version: string | null;
  error: AnalysisStageError | null;
  text_is_untrusted: boolean;
  sensitive_data_categories: SensitiveDataCategory[];
}

export interface MediaAnalysisResult {
  id: string;
  project_id: string;
  platform_id: string;
  source_message_id: string;
  visitor_id: string;
  source_media_record_id: string;
  media_type: MediaAnalysisType;
  media_sha256: string;
  mime_type: string;
  status: MediaAnalysisStatus;
  normalized_text: string | null;
  normalized_text_is_untrusted: boolean;
  sensitive_data_categories: SensitiveDataCategory[];
  transcript: string | null;
  ocr_text: string | null;
  vision_summary: string | null;
  stages: AnalysisStageResult[];
  can_continue: boolean;
  requires_handoff: boolean;
  fallback_message: string | null;
  pipeline_version: string;
  request_id: string | null;
  input_fingerprint: string;
  created_at: string;
  updated_at: string;
}

export type IntentName =
  | 'product_inquiry'
  | 'pricing_promotion'
  | 'order_assistance'
  | 'order_query'
  | 'logistics_query'
  | 'payment_issue'
  | 'after_sales_issue'
  | 'refund_return_inquiry'
  | 'complaint'
  | 'sales_lead'
  | 'human_handoff'
  | 'unknown';

export type RiskLevel = 'low' | 'medium' | 'high';

export type IntentRoute =
  | 'auto_reply'
  | 'read_only_tool'
  | 'clarify'
  | 'human_handoff';

export type RoutingReason =
  | 'high_confidence_faq'
  | 'high_confidence_read_only'
  | 'medium_confidence'
  | 'low_confidence'
  | 'high_risk'
  | 'sensitive_intent'
  | 'media_processing_failed'
  | 'repeated_unknown'
  | 'rule_match'
  | 'classification_failed'
  | 'sensitive_data_detected'
  | 'untrusted_media_confirmation'
  | 'medium_risk'
  | 'automation_disabled'
  | 'unknown_clarification';

export type ClassificationSource = 'model' | 'rule' | 'fail_closed';

export interface IntentEntities {
  order_no: string | null;
  product_name: string | null;
  sku: string | null;
  logistics_no: string | null;
  payment_reference: string | null;
  issue_summary: string | null;
}

export interface MessageIntentResult {
  id: string;
  project_id: string;
  platform_id: string;
  source_message_id: string;
  visitor_id: string;
  media_analysis_result_id: string | null;
  intent: IntentName;
  confidence: number;
  entities: IntentEntities;
  risk_level: RiskLevel;
  recommended_route: IntentRoute;
  need_human: boolean;
  taxonomy_version: 'v1';
  routing_reason: RoutingReason;
  classification_source: ClassificationSource;
  classifier_version: string;
  policy_version: string;
  request_id: string | null;
  input_fingerprint: string;
  created_at: string;
  updated_at: string;
}

export interface CombinedMessageAnalysis {
  source_message_id: string;
  media: MediaAnalysisResult | null;
  intent: MessageIntentResult | null;
}

/**
 * Explicit UI state. Missing data never silently becomes a successful result.
 */
export type MessageAnalysisViewState =
  | { status: 'available'; analysis: CombinedMessageAnalysis }
  | { status: 'loading' }
  | { status: 'not_found' }
  | { status: 'error' }
  | { status: 'unavailable'; reason: 'employee_read_endpoint_required' };

export const MESSAGE_ANALYSIS_UNAVAILABLE: MessageAnalysisViewState = {
  status: 'unavailable',
  reason: 'employee_read_endpoint_required',
};
