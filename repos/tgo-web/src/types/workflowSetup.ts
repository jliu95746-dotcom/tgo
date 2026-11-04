import type { Agent } from '@/types';

export type WorkflowAgentCandidate = Pick<Agent, 'id' | 'status' | 'llmModel' | 'knowledgeBases'>;

export interface WorkflowBusinessReference {
  kind: 'input' | 'agent_answer' | 'api_result';
  label: string;
}
