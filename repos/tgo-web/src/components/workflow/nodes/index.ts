/**
 * Workflow Nodes Index
 * Export all custom node components
 */

export { default as InputNode } from './InputNode';
export { default as TimerNode } from './TimerNode';
export { default as WebhookNode } from './WebhookNode';
export { default as EventNode } from './EventNode';
export { default as AnswerNode } from './AnswerNode';
export { default as AgentNode } from './AgentNode';
export { default as ApprovalNode } from './ApprovalNode';
export { default as ToolNode } from './ToolNode';
export { default as ConditionNode } from './ConditionNode';
export { default as LLMNode } from './LLMNode';
export { default as ParallelNode } from './ParallelNode';
export { default as APINode } from './APINode';
export { default as BusinessQueryNode } from './BusinessQueryNode';
export { default as ClassifierNode } from './ClassifierNode';

import InputNode from './InputNode';
import TimerNode from './TimerNode';
import WebhookNode from './WebhookNode';
import EventNode from './EventNode';
import AnswerNode from './AnswerNode';
import AgentNode from './AgentNode';
import ApprovalNode from './ApprovalNode';
import ToolNode from './ToolNode';
import ConditionNode from './ConditionNode';
import LLMNode from './LLMNode';
import ParallelNode from './ParallelNode';
import APINode from './APINode';
import BusinessQueryNode from './BusinessQueryNode';
import ClassifierNode from './ClassifierNode';

/**
 * Node types map for React Flow
 */
export const nodeTypes = {
  input: InputNode,
  timer: TimerNode,
  webhook: WebhookNode,
  event: EventNode,
  answer: AnswerNode,
  agent: AgentNode,
  approval: ApprovalNode,
  tool: ToolNode,
  condition: ConditionNode,
  llm: LLMNode,
  parallel: ParallelNode,
  api: APINode,
  business_query: BusinessQueryNode,
  classifier: ClassifierNode,
};
