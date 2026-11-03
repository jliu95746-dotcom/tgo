import type { AgentBusinessCapabilities, BusinessCustomerTestSession, BusinessRequestState, BusinessReviewRecord, BusinessReviewRequest, BusinessReviewRequestState, BusinessReviewState, BusinessTrialReport } from '@/types/agentBusinessTrials';

const hash=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const time=(value:unknown)=>typeof value==='string'&&Number.isFinite(Date.parse(value));
const id=(value:unknown)=>typeof value==='string'&&value.length>0&&value.length<=80;
const operation=(value:unknown)=>value==='order_query'||value==='logistics_query'||value==='customer_profile_query'||value==='membership_query';
const revision=(value:unknown)=>typeof value==='number'&&Number.isInteger(value)&&value>=1;
const containsOnly=(value:object,keys:string[])=>Object.keys(value).every(key=>keys.includes(key));
export const freshBusinessCheck=(at:string)=>time(at)&&Date.now()-Date.parse(at)>=-30000&&Date.now()-Date.parse(at)<=60000;

export function validBusinessReviewCommand(value:unknown):value is BusinessReviewRequest {
  if(!value||typeof value!=='object'||!('request_id' in value)||!('expected_latest_review_id' in value)||!('decision' in value)||!('checks' in value)||!('reason' in value))return false;
  const checks=value.checks;
  return id(value.request_id)&&(value.expected_latest_review_id===null||id(value.expected_latest_review_id))&&Boolean(checks&&typeof checks==='object'&&
    ['ownership_checked','result_checked','readonly_scope_checked'].every(key=>key in checks&&typeof (checks as Record<string,unknown>)[key]==='boolean')&&
    containsOnly(checks,['ownership_checked','result_checked','readonly_scope_checked']))&&
    (value.decision==='passed'?value.reason===null:value.decision==='rejected'&&['wrong_result','needs_mapping','scope_mismatch','other'].includes(String(value.reason)))&&
    containsOnly(value,['request_id','expected_latest_review_id','decision','checks','reason']);
}
export function sameBusinessReviewCommand(left:BusinessReviewRequest,right:BusinessReviewRequest):boolean {
  return left.request_id===right.request_id&&left.expected_latest_review_id===right.expected_latest_review_id&&left.decision===right.decision&&left.reason===right.reason&&
    (Object.keys(left.checks) as Array<keyof typeof left.checks>).every(key=>left.checks[key]===right.checks[key]);
}
export function validBusinessReviewRequestState(value:BusinessReviewRequestState,report:BusinessTrialReport,actor:string):boolean {
  if(!value||value.scope!=='employee_business_review_request'||value.can_enable!==false||value.project_id!==report.project_id||value.actor_id!==actor||value.agent_id!==report.agent_id||value.report_id!==report.id||!id(value.request_id)||
    !['pending','completed','failed','cancelled'].includes(value.status)||!time(value.created_at)||!time(value.deadline_at)||!freshBusinessCheck(value.checked_at)||typeof value.can_resume!=='boolean'||typeof value.processing!=='boolean'||value.processing&&value.status!=='pending'||
    !containsOnly(value,['scope','can_enable','project_id','actor_id','agent_id','report_id','request_id','status','created_at','deadline_at','completed_at','checked_at','can_resume','processing','error_code','command','result']))return false;
  if(value.command!==null&&(!validBusinessReviewCommand(value.command)||value.command.request_id!==value.request_id))return false;
  if(value.can_resume!==(value.status==='pending'&&!value.processing&&value.command!==null&&Date.parse(value.deadline_at)>Date.parse(value.checked_at)))return false;
  if(value.status==='pending')return value.command!==null&&value.completed_at===null&&value.error_code===null&&value.result===null;
  if(!time(value.completed_at))return false;
  if(value.status==='completed')return value.error_code===null&&Boolean(value.result&&validBusinessReview(value.result,report,actor)&&value.result.request_id===value.request_id&&
    (!value.command||value.command.decision===value.result.decision&&value.command.reason===value.result.reason&&(Object.keys(value.command.checks) as Array<keyof typeof value.command.checks>).every(key=>value.command!.checks[key]===value.result!.checks[key])));
  return value.result===null&&(value.status==='cancelled'?value.error_code==='cancelled':['review_timeout','review_failed','interrupted'].includes(value.error_code??''));
}

export function validBusinessRequestState(value:BusinessRequestState,project:string,actor:string,agent:string):boolean {
  if(!value||value.scope!=='employee_business_trial_request'||value.can_enable!==false||value.project_id!==project||value.actor_id!==actor||value.agent_id!==agent||
    !id(value.request_id)||!id(value.connection_id)||!operation(value.operation)||!time(value.created_at)||!time(value.deadline_at)||
    !['preparing','ready','failed','cancelled'].includes(value.status)||!containsOnly(value,['scope','can_enable','project_id','actor_id','agent_id','request_id','connection_id','operation','status','created_at','deadline_at','completed_at','error_code','report']))return false;
  if(value.status==='preparing')return value.completed_at===null&&value.error_code===null&&value.report===null;
  if(!time(value.completed_at))return false;
  if(value.status==='ready')return value.error_code===null&&Boolean(value.report&&validBusinessReport(value.report,project,actor,agent)&&value.report.request_id===value.request_id&&value.report.connection_id===value.connection_id&&value.report.operation===value.operation);
  return value.report===null&&(value.status==='cancelled'?value.error_code==='cancelled':['preparation_timeout','preparation_failed','interrupted'].includes(value.error_code??''));
}

export function validCustomerSession(value:BusinessCustomerTestSession,project:string,connection:string):boolean {
  return Boolean(value&&value.scope==='business_customer_identity'&&value.can_enable===false&&value.project_id===project&&value.connection_id===connection&&
    id(value.binding_id)&&id(value.visitor_id)&&id(value.platform_id)&&revision(value.revision)&&time(value.expires_at)&&Date.parse(value.expires_at)>Date.now()&&
    typeof value.session_token==='string'&&value.session_token.length>=8&&value.session_token.length<=8192&&
    containsOnly(value,['scope','can_enable','binding_id','project_id','connection_id','visitor_id','platform_id','revision','expires_at','session_token']));
}
export function validBusinessReport(value:BusinessTrialReport,project:string,actor:string,agent:string):boolean {
  if(!value||value.scope!=='employee_business_trial'||value.can_enable!==false||value.assessment!=='not_reviewed'||value.project_id!==project||value.actor_id!==actor||value.agent_id!==agent||
    !id(value.id)||!id(value.request_id)||!id(value.connection_id)||!operation(value.operation)||!hash(value.configuration_fingerprint)||!time(value.created_at)||!time(value.expires_at)||
    !['running','completed','failed','cancelled'].includes(value.status)||!containsOnly(value,['scope','can_enable','assessment','id','project_id','actor_id','agent_id','connection_id','request_id','operation','status','configuration_fingerprint','created_at','expires_at','completed_at','error_code','receipt']))return false;
  if(value.status==='running')return value.receipt===null&&value.completed_at===null&&value.error_code===null;
  if(!time(value.completed_at))return false;
  if(value.status!=='completed')return value.receipt===null&&typeof value.error_code==='string';
  const receipt=value.receipt;
  if(!receipt||receipt.scope!=='employee_business_trial'||receipt.can_enable!==false||receipt.mode!=='http'||receipt.report_id!==value.id||receipt.project_id!==project||receipt.actor_id!==actor||receipt.agent_id!==agent||receipt.connection_id!==value.connection_id||receipt.request_id!==value.request_id||receipt.operation!==value.operation||receipt.configuration_fingerprint!==value.configuration_fingerprint||!time(receipt.checked_at)||!time(receipt.configuration_test_valid_until))return false;
  if(!containsOnly(receipt,['scope','can_enable','mode','project_id','actor_id','agent_id','connection_id','report_id','request_id','attempt_id','operation','binding_id','binding_revision','configuration_fingerprint','expected_revision','configuration_revision','authorization_revision','resource_revision','configuration_test_id','configuration_test_valid_until','outcome','summary','error_code','customer_evidence','checked_at']))return false;
  if(!id(receipt.attempt_id)||!id(receipt.binding_id)||!id(receipt.configuration_test_id)||![receipt.binding_revision,receipt.expected_revision,receipt.configuration_revision,receipt.authorization_revision].every(revision)||!hash(receipt.resource_revision)||receipt.error_code!==value.error_code)return false;
  const evidence=receipt.customer_evidence;
  if(!evidence||!id(evidence.binding_id)||!revision(evidence.binding_revision)||!revision(evidence.policy_revision)||!time(evidence.expires_at)||Date.parse(evidence.expires_at)<=Date.parse(receipt.checked_at)||!containsOnly(evidence,['binding_id','binding_revision','policy_revision','expires_at']))return false;
  if(receipt.outcome==='failed')return receipt.summary===null&&typeof receipt.error_code==='string';
  return receipt.outcome==='passed'&&receipt.error_code===null&&Boolean(receipt.summary&&typeof receipt.summary.status==='string'&&
    containsOnly(receipt.summary,['status','amount_minor','currency','carrier','tracking_no_masked','segment','service_tags','tier','points_balance'])&&
    (value.operation==='membership_query'?typeof receipt.summary.tier==='string'&&typeof receipt.summary.points_balance==='number'&&Number.isInteger(receipt.summary.points_balance)&&receipt.summary.points_balance>=0:
      value.operation==='customer_profile_query'?(receipt.summary.segment===null||typeof receipt.summary.segment==='string')&&Array.isArray(receipt.summary.service_tags)&&receipt.summary.service_tags.length<=8:true));
}
export function validBusinessReview(record:BusinessReviewRecord,report:BusinessTrialReport,actor:string):boolean {
  return Boolean(record&&record.scope==='employee_business_operation_review'&&record.can_enable===false&&record.project_id===report.project_id&&record.agent_id===report.agent_id&&record.report_id===report.id&&record.connection_id===report.connection_id&&record.operation===report.operation&&record.reviewed_by===actor&&
    id(record.id)&&id(record.request_id)&&revision(record.revision)&&record.criteria_version===1&&hash(record.report_digest)&&record.configuration_fingerprint===report.configuration_fingerprint&&
    ['passed','rejected'].includes(record.decision)&&record.checks&&['ownership_checked','result_checked','readonly_scope_checked'].every(key=>typeof record.checks[key as keyof typeof record.checks]==='boolean')&&
    containsOnly(record.checks,['ownership_checked','result_checked','readonly_scope_checked'])&&time(record.created_at)&&time(record.valid_until)&&
    (record.decision==='passed'?record.reason===null&&Object.values(record.checks).every(Boolean):['wrong_result','needs_mapping','scope_mismatch','other'].includes(record.reason??''))&&
    containsOnly(record,['scope','can_enable','id','project_id','agent_id','report_id','connection_id','operation','reviewed_by','request_id','revision','criteria_version','report_digest','configuration_fingerprint','decision','checks','reason','created_at','valid_until']));
}
export function validBusinessReviewState(value:BusinessReviewState,report:BusinessTrialReport,actor:string):boolean {
  if(!value||value.scope!=='employee_business_operation_review'||value.can_enable!==false||value.project_id!==report.project_id||value.agent_id!==report.agent_id||value.report_id!==report.id||value.checked_by!==actor||!freshBusinessCheck(value.checked_at)||
    !['current','changed','not_eligible','unavailable'].includes(value.status)||!['not_reviewed','passed','rejected','needs_retest','unavailable'].includes(value.review_status)||value.can_review!==(value.status==='current')||!Array.isArray(value.issues)||!Array.isArray(value.reviews)||value.reviews.length>50||value.reviews.some(row=>!validBusinessReview(row,report,actor)))return false;
  if(value.status==='current'&&(!time(value.valid_until)||Date.parse(value.valid_until!)<=Date.now()||value.issues.length))return false;
  if(value.latest_review!==null&&(!value.reviews.length||value.latest_review.id!==value.reviews[0].id||!validBusinessReview(value.latest_review,report,actor)))return false;
  return value.review_status!=='passed'||Boolean(value.status==='current'&&value.latest_review?.decision==='passed'&&Date.parse(value.latest_review.valid_until)>Date.now());
}
export function validBusinessCapabilities(value:AgentBusinessCapabilities,project:string,actor:string,agent:string):boolean {
  if(!value||value.scope!=='employee_business_capabilities'||value.can_enable!==false||value.can_execute!==false||value.project_id!==project||value.agent_id!==agent||value.checked_by!==actor||!freshBusinessCheck(value.checked_at)||!Array.isArray(value.capabilities)||value.capabilities.length>200||
    !containsOnly(value,['scope','can_enable','can_execute','project_id','agent_id','checked_by','checked_at','capabilities']))return false;
  const seen=new Set<string>();
  return value.capabilities.every(row=>{const key=`${row.binding_id}:${row.operation}`;
    if(seen.has(key)||!id(row.binding_id)||!id(row.connection_id)||!revision(row.binding_revision)||!operation(row.operation)||!Array.isArray(row.issues)||!['not_tested','testing','test_failed','review_required','rejected','passed','needs_retest','unavailable'].includes(row.status)||
      !containsOnly(row,['binding_id','binding_revision','connection_id','operation','status','report_id','review_id','valid_until','issues']))return false;
    seen.add(key);return row.status!=='passed'||Boolean(id(row.report_id)&&id(row.review_id)&&time(row.valid_until)&&Date.parse(row.valid_until!)>Date.now()&&!row.issues.length);
  });
}
