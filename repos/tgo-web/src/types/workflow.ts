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
  | 'tool' 
  | 'condition' 
  | 'llm' 
  | 'parallel'
  | 'api'
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
export interface InputNodeData extends BaseNodeData {
  type: 'input';
  input_variables?: { name: string; type: 'string' | 'number' | 'boolean'; description?: string }[];
}

/**
 * Timer node data - scheduled trigger
 */
export interface TimerNodeData extends BaseNodeData {
  type: 'timer';
  cron_expression: string;
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
  | ToolNodeData 
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
  | 'completed' 
  | 'failed' 
  | 'cancelled';

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
  name?: string | null;
  description?: string | null;
  nodes?: WorkflowNode[] | null;
  edges?: WorkflowEdge[] | null;
  status?: WorkflowStatus | null;
  tags?: string[] | null;
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
    input_variables: [{ name: 'query', type: 'string', description: 'workflow.placeholders.input_query_desc' }],
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
