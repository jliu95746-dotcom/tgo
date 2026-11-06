/**
 * Workflow Type Definitions
 * Defines all types related to AI Agent Workflows
 */

import type { Node } from 'reactflow';

// ============================================================================
// Node Types
// ============================================================================

/**
 * Available workflow node types
 */
export type WorkflowNodeType = 
  | 'input'      // 用户输入触发
  | 'timer'      // 定时触发
  | 'webhook'    // Webhook 触发
  | 'event'      // 事件触发
  | 'answer'     // 回复/输出节点
  | 'agent' 
  | 'approval'
  | 'tool' 
  | 'condition' 
  | 'llm' 
  | 'parallel'
  | 'api'
  | 'business_query'
  | 'classifier';

/**
 * Base node data shared by all nodes
 */
export interface BaseNodeData {
  label: string;
  description?: string;
  reference_key?: string; // Stable English key for variable references, e.g., "llm_1"
}

/**
 * API node data - external HTTP call
 */
export interface APINodeData extends BaseNodeData {
  type: 'api';
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  url: string;
  headers?: { key: string; value: string }[];
  params?: { key: string; value: string }[];
  body_type: 'none' | 'json' | 'form-data' | 'x-www-form-urlencoded' | 'raw';
  body?: string; // For json and raw
  form_data?: { key: string; value: string; type: 'text' | 'file' }[];
  form_url_encoded?: { key: string; value: string }[];
  raw_type?: 'text' | 'html' | 'xml' | 'javascript';
}

/**
 * Input node data - user input trigger
 */
export interface WorkflowInputVariable {
  name: string;
  type: 'string' | 'number' | 'boolean';
  description?: string;
  required?: boolean;
  min_length?: number;
  max_length?: number;
  minimum?: number;
  maximum?: number;
  allowed_values?: string[];
}

export interface InputNodeData extends BaseNodeData {
  type: 'input';
  input_variables?: WorkflowInputVariable[];
}

/**
 * Timer node data - scheduled trigger
 */
export interface TimerNodeData extends BaseNodeData {
  type: 'timer';
  cron_expression: string;
  /** IANA timezone. Legacy definitions without this field use UTC. */
  timezone?: string;
}

/**
 * Webhook node data - HTTP callback trigger
 */
export interface WebhookNodeData extends BaseNodeData {
  type: 'webhook';
  path?: string; // Optional path suffix
  method?: 'GET' | 'POST';
}

/**
 * Event node data - internal system event trigger
 */
export interface EventNodeData extends BaseNodeData {
  type: 'event';
  event_type: string;
}

/**
 * Answer node data - response point of workflow
 */
export interface AnswerNodeData extends BaseNodeData {
  type: 'answer';
  output_type: 'variable' | 'template' | 'structured';
  output_variable?: string; // For 'variable' type
  output_template?: string; // For 'template' type
  output_structure?: { key: string; value: string }[]; // For 'structured' type
}

/**
 * Agent node data - calls another AI Agent
 */
export interface AgentNodeData extends BaseNodeData {
  type: 'agent';
  agent_id: string;
  agent_name?: string;
  input_mapping?: Record<string, string>;
}

export interface ApprovalNodeData extends BaseNodeData {
  type: 'approval';
  approver_staff_id: string;
  summary: string;
  expires_in_minutes: number;
  after_approval_action?: 'create_aftersales_review';
  case_category?: 'return_request' | 'exchange_request' | 'quality_issue';
}

/**
 * Tool node data - executes an Tool tool
 */
export interface ToolNodeData extends BaseNodeData {
  type: 'tool';
  tool_id: string;
  tool_name?: string;
  config?: Record<string, any>;
  input_mapping?: Record<string, string>;
}

export interface BusinessQueryNodeData extends BaseNodeData {
  type: 'business_query';
  connection_id: string;
  operation: 'order_query' | 'logistics_query';
  order_variable: 'input.order_no';
}

/**
 * Condition node data - branching logic
 */
export interface ConditionNodeData extends BaseNodeData {
  type: 'condition';
  condition_type: 'expression' | 'variable' | 'llm';
  expression?: string;
  variable?: string;
  operator?: 'equals' | 'notEquals' | 'contains' | 'greaterThan' | 'lessThan' | 'isEmpty' | 'isNotEmpty';
  compare_value?: string;
  llm_prompt?: string;
  provider_id?: string;
  model_id?: string;
  model_name?: string;
}

/**
 * LLM node data - direct LLM call
 */
export interface LLMNodeData extends BaseNodeData {
  type: 'llm';
  provider_id?: string;
  model_id?: string;
  model_name?: string;
  system_prompt?: string;
  user_prompt: string;
  temperature?: number;
  max_tokens?: number;
  tool_ids?: string[]; // IDs of selected Tool tools
  collection_ids?: string[]; // IDs of selected knowledge bases
}

/**
 * Parallel node data - parallel execution
 */
export interface ParallelNodeData extends BaseNodeData {
  type: 'parallel';
  branches: number;
  wait_for_all: boolean;
  timeout?: number;
}

/**
 * Classifier node data - categorizes input using LLM
 */
export interface ClassifierNodeData extends BaseNodeData {
  type: 'classifier';
  input_variable: string;
  provider_id?: string;
  model_id?: string;
  model_name?: string;
  categories: { id: string; name: string; description: string }[];
}

/**
 * Union type for all node data
 */
export type WorkflowNodeData = 
  | InputNodeData
  | TimerNodeData
  | WebhookNodeData
  | EventNodeData
  | AnswerNodeData
  | AgentNodeData 
  | ApprovalNodeData
  | ToolNodeData 
  | BusinessQueryNodeData
  | ConditionNodeData 
  | LLMNodeData 
  | ParallelNodeData
  | APINodeData
  | ClassifierNodeData;

/**
 * Workflow node extending React Flow Node
 */
export type WorkflowNode = Node<WorkflowNodeData, WorkflowNodeType>;

// ============================================================================
// Edge Types
// ============================================================================

/**
 * Edge label for conditional branches
 */
export type EdgeLabel = 'true' | 'false' | 'default' | string;

/**
 * Workflow edge extending React Flow Edge
 */
export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  type?: string;
  data?: Record<string, any>;
}

// ============================================================================
// Workflow Types
// ============================================================================

/**
 * Workflow status
 */
export type WorkflowStatus = 'draft' | 'active' | 'inactive' | 'archived';

/**
 * Workflow execution status
 */
export type WorkflowExecutionStatus = 
  | 'pending' 
  | 'running' 
  | 'waiting_approval'
  | 'pending_resume'
  | 'completed' 
  | 'failed' 
  | 'rejected'
  | 'expired'
  | 'cancelled';

export interface WorkflowApprovalSummary {
  id: string;
  execution_id: string;
  node_id: string;
  summary: string;
  preview_digest: string;
  status: 'pending' | 'approved';
  requested_at: string;
  expires_at: string;
  customer_session_available: boolean;
}

export interface WorkflowApprovalDecisionResult {
  id: string;
  execution_id: string;
  status: 'approved' | 'rejected';
  dispatch_status: 'queued' | 'unknown' | 'not_applicable';
}

export interface WorkflowReconciliationNode {
  id: string;
  node_id: string;
  node_type: string;
  label: string;
  status: string;
  started_at: string;
  completed_at: string | null;
}

export interface WorkflowReconciliationSummary {
  id: string;
  execution_id: string;
  workflow_id: string;
  summary: string;
  review_digest: string;
  created_at: string;
  requested_at: string;
  can_continue: boolean;
  blocked_reason: 'unknown_result' | 'progress_missing' | 'record_changed' | 'key_unavailable' | null;
  nodes: WorkflowReconciliationNode[];
}

export interface WorkflowReconciliationDecisionResult {
  id: string;
  execution_id: string;
  decision: 'continue' | 'stop';
  status: 'pending_resume' | 'cancelled';
  decided_at: string;
  dispatch_status: 'queued' | 'unknown' | 'not_applicable';
}

export interface WorkflowAfterSalesReviewSummary {
  id: string;
  execution_id: string;
  approval_id: string;
  visitor_session_id: string;
  category: 'return_request' | 'exchange_request' | 'quality_issue';
  review_digest: string;
  status: 'pending_staff_review' | 'reviewed';
  created_at: string;
  review_result: WorkflowAfterSalesReviewResult | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export type WorkflowAfterSalesReviewResult =
  | 'needs_manual_service'
  | 'needs_customer_information'
  | 'not_aftersales';

export interface WorkflowAfterSalesEvidenceFile {
  id: string;
  file_name: string;
  file_type: string;
  created_at: string;
}

export interface WorkflowAfterSalesChatTarget {
  channel_type: 251;
  channel_id: string;
  message_seq?: number | null;
}

export interface WorkflowAfterSalesIntakeCreate {
  review_digest: string;
  order_reference: string;
  sku: string;
  request_reason: string;
  policy_reference: string;
  evidence_file_ids: string[];
}

export interface WorkflowAfterSalesIntakeSummary {
  review_id: string;
  order_reference: string;
  sku: string;
  request_reason: string;
  policy_reference: string;
  evidence_file_ids: string[];
  prepared_by: string;
  prepared_at: string;
  status: 'prepared_internal';
  external_submission_status: 'not_submitted';
}

export interface WorkflowAfterSalesHandoffSummary {
  request_id: string;
  status: string;
  notification_status: 'submitted' | 'unknown';
}

/**
 * Workflow definition
 */
export interface WorkflowDefinition {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

/**
 * Workflow record from DB
 */
export interface Workflow {
  published_version?: number | null;
  publication_revision?: number;
  id: string;
  name: string;
  description: string | null;
  definition: WorkflowDefinition;
  status: WorkflowStatus;
  version: number;
  tags: string[];
  created_at: string;
  updated_at: string;
  created_by?: string | null;
}

/**
 * Workflow summary for list views
 */
export interface WorkflowSummary {
  published_version?: number | null;
  publication_revision?: number;
  id: string;
  name: string;
  description: string | null;
  status: WorkflowStatus;
  version: number;
  tags: string[];
  updated_at: string;
}

/**
 * Workflow execution record
 */
export type WorkflowJsonValue =
  | string
  | number
  | boolean
  | null
  | WorkflowJsonValue[]
  | { [key: string]: WorkflowJsonValue };

export interface WorkflowExecution {
  release_id?: string | null;
  employee_agent_id?: string | null;
  definition_digest?: string | null;
  ai_request_id?: string | null;
  business_request_id?: string | null;
  visitor_session_id?: string | null;
  source_message_id?: string | null;
  source_channel?: string | null;
  parent_execution_id?: string | null;
  workflow_lineage?: string[] | null;
  execution_mode?: 'draft_test' | 'production' | null;
  workflow_version?: number | null;
  id: string;
  workflow_id: string;
  status: WorkflowExecutionStatus;
  input?: Record<string, any> | null;
  output?: WorkflowJsonValue;
  error?: string | null;
  started_at: string;
  completed_at?: string | null;
  duration?: number | null;
  node_executions: NodeExecution[];
}

export interface WorkflowBusinessQueryAuditItem {
  id: string;
  project_id: string;
  query_id: string;
  agent_id: string;
  operation: 'order_query' | 'logistics_query' | 'customer_profile_query' | 'membership_query';
  outcome: 'success' | 'denied' | 'timeout' | 'failed';
  duration_ms: number;
  created_at: string;
}

export interface WorkflowBusinessQueryAuditList {
  events: WorkflowBusinessQueryAuditItem[];
  total: number;
}

export interface WorkflowCustomerMessageLookup {
  visitor_session_id: string;
  source_message_id: string;
}

export interface WorkflowChannelMessageLookup {
  channel_id: string;
  source_message_id: string;
}

export interface WorkflowCapabilities {
  nodes: {
    type: string;
    available: boolean;
    preparation_available?: boolean;
    required_fields: string[];
    reason: string | null;
  }[];
  execution_modes: ('draft_test' | 'production')[];
}

/**
 * Workflow execution stream events
 */
export type WorkflowStreamEvent = 
  | { event: 'workflow_started'; workflow_run_id: string; data: { id: string; workflow_id: string; inputs: any; created_at: number } }
  | { event: 'node_started'; workflow_run_id: string; data: { id: string; node_id: string; node_type: string; title: string; index: number } }
  | { event: 'node_finished'; workflow_run_id: string; data: { id: string; node_id: string; node_type: string; status: 'succeeded' | 'failed'; inputs: any; outputs: WorkflowJsonValue; error?: string; elapsed_time: number } }
  | { event: 'workflow_finished'; workflow_run_id: string; data: { status: 'succeeded' | 'failed' | 'cancelled'; outputs: WorkflowJsonValue; error?: string; total_steps: number; elapsed_time: number } };

/**
 * Individual node execution record
 */
export interface NodeExecution {
  id: string;
  execution_id: string;
  node_id: string;
  node_type: string;
  status: WorkflowExecutionStatus;
  input?: Record<string, any> | null;
  output?: WorkflowJsonValue;
  error?: string | null;
  started_at: string;
  completed_at?: string | null;
  duration?: number | null;
}

// ============================================================================
// API Types
// ============================================================================

/**
 * Workflow create request
 */
export interface WorkflowCreateRequest {
  name: string;
  description?: string | null;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  tags?: string[];
}

/**
 * Workflow update request
 */
export interface WorkflowUpdateRequest {
  expected_version?: number;
  name?: string | null;
  description?: string | null;
  nodes?: WorkflowNode[] | null;
  edges?: WorkflowEdge[] | null;
  status?: WorkflowStatus | null;
  tags?: string[] | null;
}

export interface WorkflowPublishRequest {
  expected_draft_version: number;
  expected_publication_revision: number;
}
export interface WorkflowRollbackRequest {
  target_version: number;
  expected_publication_revision: number;
}
export interface WorkflowReleaseSummary {
  id: string;
  version: number;
  draft_version: number;
  created_by: string | null;
  created_at: string;
}

/**
 * Workflow list response
 */
export interface WorkflowListResponse {
  data: WorkflowSummary[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_next: boolean;
    has_prev: boolean;
  };
}

export interface WorkflowDeviceReference {
  workflow_id: string;
  name: string;
  source: 'draft' | 'published' | 'both';
}

export interface WorkflowDeviceReferences {
  references: WorkflowDeviceReference[];
  total: number;
}

/**
 * Workflow query parameters
 */
export interface WorkflowQueryParams {
  status?: WorkflowStatus;
  search?: string;
  tags?: string[];
  limit?: number;
  offset?: number;
}

// ============================================================================
// Editor Types
// ============================================================================

/**
 * Node palette item for drag and drop
 */
export interface NodePaletteItem {
  type: WorkflowNodeType;
  label: string;
  description: string;
  icon: string;
  color: string;
  category: 'trigger' | 'ai' | 'logic' | 'external' | 'output';
}

// ============================================================================
// Editor Types
// ============================================================================

/**
 * Editor state
 */
export interface WorkflowEditorState {
  workflow: Workflow | null;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  isDirty: boolean;
  isValid: boolean;
  validationErrors: ValidationError[];
  history: HistoryState[];
  historyIndex: number;
}

/**
 * Validation error
 */
export interface ValidationError {
  nodeId?: string;
  edgeId?: string;
  message: string;
  severity: 'error' | 'warning';
}

/**
 * History state for undo/redo
 */
export interface HistoryState {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  timestamp: number;
}

// ============================================================================
// Constants
// ============================================================================

/**
 * Node type configurations
 */
export const NODE_TYPE_CONFIG: Record<WorkflowNodeType, NodePaletteItem> = {
  input: {
    type: 'input',
    label: 'workflow.node_types.input.label',
    description: 'workflow.node_types.input.description',
    icon: 'Play',
    color: 'green',
    category: 'trigger',
  },
  timer: {
    type: 'timer',
    label: 'workflow.node_types.timer.label',
    description: 'workflow.node_types.timer.description',
    icon: 'Clock',
    color: 'green',
    category: 'trigger',
  },
  webhook: {
    type: 'webhook',
    label: 'workflow.node_types.webhook.label',
    description: 'workflow.node_types.webhook.description',
    icon: 'Globe',
    color: 'green',
    category: 'trigger',
  },
  event: {
    type: 'event',
    label: 'workflow.node_types.event.label',
    description: 'workflow.node_types.event.description',
    icon: 'Zap',
    color: 'green',
    category: 'trigger',
  },
  answer: {
    type: 'answer',
    label: 'workflow.node_types.answer.label',
    description: 'workflow.node_types.answer.description',
    icon: 'MessageSquare',
    color: 'blue',
    category: 'output',
  },
  agent: {
    type: 'agent',
    label: 'workflow.node_types.agent.label',
    description: 'workflow.node_types.agent.description',
    icon: 'Bot',
    color: 'blue',
    category: 'ai',
  },
  approval: {
    type: 'approval',
    label: 'workflow.node_types.approval.label',
    description: 'workflow.node_types.approval.description',
    icon: 'ShieldCheck',
    color: 'purple',
    category: 'logic',
  },
  tool: {
    type: 'tool',
    label: 'workflow.node_types.tool.label',
    description: 'workflow.node_types.tool.description',
    icon: 'Wrench',
    color: 'orange',
    category: 'external',
  },
  condition: {
    type: 'condition',
    label: 'workflow.node_types.condition.label',
    description: 'workflow.node_types.condition.description',
    icon: 'GitBranch',
    color: 'purple',
    category: 'logic',
  },
  llm: {
    type: 'llm',
    label: 'workflow.node_types.llm.label',
    description: 'workflow.node_types.llm.description',
    icon: 'MessageSquare',
    color: 'cyan',
    category: 'ai',
  },
  parallel: {
    type: 'parallel',
    label: 'workflow.node_types.parallel.label',
    description: 'workflow.node_types.parallel.description',
    icon: 'GitMerge',
    color: 'indigo',
    category: 'logic',
  },
  api: {
    type: 'api',
    label: 'workflow.node_types.api.label',
    description: 'workflow.node_types.api.description',
    icon: 'Globe',
    color: 'blue',
    category: 'external',
  },
  business_query: {
    type: 'business_query',
    label: 'workflow.node_types.business_query.label',
    description: 'workflow.node_types.business_query.description',
    icon: 'ShieldCheck',
    color: 'blue',
    category: 'external',
  },
  classifier: {
    type: 'classifier',
    label: 'workflow.node_types.classifier.label',
    description: 'workflow.node_types.classifier.description',
    icon: 'LayoutGrid',
    color: 'orange',
    category: 'logic',
  },
};

/**
 * Default node data by type
 */
export const DEFAULT_NODE_DATA: Record<WorkflowNodeType, WorkflowNodeData> = {
  input: {
    type: 'input',
    label: 'workflow.node_types.input.label',
    input_variables: [{ name: 'query', type: 'string', description: 'workflow.placeholders.input_query_desc', required: true }],
  },
  timer: {
    type: 'timer',
    label: 'workflow.node_types.timer.label',
    cron_expression: '0 * * * *',
  },
  webhook: {
    type: 'webhook',
    label: 'workflow.node_types.webhook.label',
    path: '',
    method: 'POST',
  },
  event: {
    type: 'event',
    label: 'workflow.node_types.event.label',
    event_type: '',
  },
  answer: {
    type: 'answer',
    label: 'workflow.node_types.answer.label',
    output_type: 'template',
    output_template: '',
  },
  agent: {
    type: 'agent',
    label: 'workflow.node_types.agent.label',
    agent_id: '',
  },
  approval: {
    type: 'approval',
    label: 'workflow.node_types.approval.label',
    approver_staff_id: '',
    summary: '',
    expires_in_minutes: 1440,
  },
  tool: {
    type: 'tool',
    label: 'workflow.node_types.tool.label',
    tool_id: '',
  },
  condition: {
    type: 'condition',
    label: 'workflow.node_types.condition.label',
    condition_type: 'expression',
    expression: '',
  },
  llm: {
    type: 'llm',
    label: 'workflow.node_types.llm.label',
    user_prompt: '',
    tool_ids: [],
    collection_ids: [],
    temperature: 0.7,
    max_tokens: 2000,
  },
  parallel: {
    type: 'parallel',
    label: 'workflow.node_types.parallel.label',
    branches: 2,
    wait_for_all: true,
  },
  api: {
    type: 'api',
    label: 'workflow.node_types.api.label',
    method: 'GET',
    url: '',
    headers: [],
    params: [],
    body_type: 'json',
  },
  business_query: {
    type: 'business_query',
    label: 'workflow.node_types.business_query.label',
    connection_id: '', operation: 'order_query', order_variable: 'input.order_no',
  },
  classifier: {
    type: 'classifier',
    label: 'workflow.node_types.classifier.label',
    input_variable: '',
    categories: [
      { id: 'cat_1', name: 'workflow.classifier.default_category_1', description: 'workflow.classifier.default_category_desc' },
      { id: 'cat_2', name: 'workflow.classifier.default_category_2', description: 'workflow.classifier.default_category_desc' },
    ],
  },
};
