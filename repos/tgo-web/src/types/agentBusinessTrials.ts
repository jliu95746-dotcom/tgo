import type { BusinessCapability, BusinessConnectionTestSummary } from './businessConnections';

export interface BusinessEmployeeCurrentState {
  scope:'employee_business_current_state';can_enable:false;project_id:string;actor_id:string;agent_id:string;active:boolean;checked_at:string;
}

export interface BusinessTrialRequest {
  request_id:string; connection_id:string; operation:BusinessCapability; visitor_id:string;
  order_no?:string; customer_session:string;
}
export interface BusinessRequestControl {request_id:string;connection_id:string;operation:BusinessCapability}
export interface BusinessRequestState extends BusinessRequestControl {
  scope:'employee_business_trial_request';can_enable:false;project_id:string;actor_id:string;agent_id:string;
  status:'preparing'|'ready'|'failed'|'cancelled';created_at:string;deadline_at:string;completed_at:string|null;
  error_code:'preparation_timeout'|'preparation_failed'|'interrupted'|'cancelled'|null;report:BusinessTrialReport|null;
}
export interface BusinessTrialReceipt {
  scope:'employee_business_trial'; can_enable:false; mode:'http'; project_id:string; actor_id:string; agent_id:string;
  connection_id:string; report_id:string; request_id:string; attempt_id:string; operation:BusinessCapability;
  binding_id:string; binding_revision:number; configuration_fingerprint:string; expected_revision:number;
  configuration_revision:number; authorization_revision:number; resource_revision:string;
  configuration_test_id:string; configuration_test_valid_until:string; outcome:'passed'|'failed';
  summary:BusinessConnectionTestSummary|null; error_code:string|null;
  customer_evidence:{binding_id:string;binding_revision:number;policy_revision:number;expires_at:string}; checked_at:string;
}
export interface BusinessTrialReport {
  scope:'employee_business_trial'; can_enable:false; assessment:'not_reviewed'; id:string; project_id:string; actor_id:string;
  agent_id:string; connection_id:string; request_id:string; operation:BusinessCapability;
  status:'running'|'completed'|'failed'|'cancelled'; configuration_fingerprint:string; created_at:string;
  expires_at:string; completed_at:string|null; error_code:string|null; receipt:BusinessTrialReceipt|null;
}
export interface BusinessCustomerTestSession {
  scope:'business_customer_identity'; can_enable:false; binding_id:string; project_id:string; connection_id:string;
  visitor_id:string; platform_id:string; revision:number; expires_at:string; session_token:string;
}
export interface BusinessReviewChecks { ownership_checked:boolean; result_checked:boolean; readonly_scope_checked:boolean }
export type BusinessReviewReason='wrong_result'|'needs_mapping'|'scope_mismatch'|'other';
export interface BusinessReviewRequest {
  request_id:string; expected_latest_review_id:string|null; decision:'passed'|'rejected'; checks:BusinessReviewChecks; reason:BusinessReviewReason|null;
}
export interface BusinessReviewRecord {
  scope:'employee_business_operation_review'; can_enable:false; id:string; project_id:string; agent_id:string; report_id:string;
  connection_id:string; operation:BusinessCapability; reviewed_by:string; request_id:string; revision:number; criteria_version:1;
  report_digest:string; configuration_fingerprint:string; decision:'passed'|'rejected'; checks:BusinessReviewChecks;
  reason:BusinessReviewReason|null; created_at:string; valid_until:string;
}
export interface BusinessReviewRequestState {
  scope:'employee_business_review_request';can_enable:false;project_id:string;actor_id:string;agent_id:string;report_id:string;request_id:string;
  status:'pending'|'completed'|'failed'|'cancelled';created_at:string;deadline_at:string;completed_at:string|null;checked_at:string;can_resume:boolean;processing:boolean;
  error_code:'review_timeout'|'review_failed'|'interrupted'|'cancelled'|null;command:BusinessReviewRequest|null;result:BusinessReviewRecord|null;
}
export interface BusinessReviewState {
  scope:'employee_business_operation_review'; can_enable:false; project_id:string; agent_id:string; report_id:string; checked_by:string; checked_at:string;
  status:'current'|'changed'|'not_eligible'|'unavailable'; review_status:'not_reviewed'|'passed'|'rejected'|'needs_retest'|'unavailable';
  issues:string[]; can_review:boolean; valid_until:string|null; latest_review:BusinessReviewRecord|null; reviews:BusinessReviewRecord[];
}
export interface BusinessOperationCapability {
  binding_id:string;binding_revision:number;connection_id:string;operation:BusinessCapability;
  status:'not_tested'|'testing'|'test_failed'|'review_required'|'rejected'|'passed'|'needs_retest'|'unavailable';
  report_id:string|null;review_id:string|null;valid_until:string|null;issues:string[];
}
export interface AgentBusinessCapabilities {
  scope:'employee_business_capabilities';can_enable:false;can_execute:false;project_id:string;agent_id:string;
  checked_by:string;checked_at:string;capabilities:BusinessOperationCapability[];
}
