import type { WorkflowAgentCandidate, WorkflowBusinessReference } from '@/types/workflowSetup';
import type { WorkflowNode, InputNodeData } from '@/types/workflow';

/** Prefill configuration only; linked knowledge and an enabled flag do not prove a successful test. */
export function selectWorkflowAgent(currentId: string, manuallySelected: boolean, agents: readonly WorkflowAgentCandidate[]): string {
  if (currentId || manuallySelected) return currentId;
  const candidates = agents.filter(agent => agent.status === 'active' && agent.llmModel?.trim() && agent.knowledgeBases.length > 0);
  return candidates.length === 1 ? candidates[0].id : '';
}

/** Translate only exact known references. Keep arbitrary expressions intact in the advanced editor. */
export function describeWorkflowReference(expression: string | undefined, nodes: readonly WorkflowNode[]): WorkflowBusinessReference | null {
  if (!expression) return null;
  const reference = expression.trim().replace(/^\{\{\s*(.*?)\s*\}\}$/, '$1');
  const parts = reference.split('.');
  if (parts.length !== 2) return null;
  const matching = nodes.filter(node => (node.data.reference_key || node.id) === parts[0]);
  if (matching.length !== 1) return null;
  const node = matching[0];
  if (node.type === 'input') {
    const variable = (node.data as InputNodeData).input_variables?.find(value => value.name === parts[1]);
    return variable ? { kind: 'input', label: variable.description?.trim() || variable.name } : null;
  }
  if (node.type === 'agent' && parts[1] === 'text') return { kind: 'agent_answer', label: node.data.label };
  if (node.type === 'api' && parts[1] === 'body') return { kind: 'api_result', label: node.data.label };
  return null;
}
