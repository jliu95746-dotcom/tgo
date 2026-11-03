import type { WorkflowCapabilities } from '@/types/workflow';

export function isWorkflowNodeAvailable(capabilities: WorkflowCapabilities | null, type: string): boolean {
  return capabilities?.nodes.some(node => node.type === type && node.available === true) === true;
}

/** Preparation is explicitly advertised by the engine; it never grants execution. */
export function isWorkflowNodeConfigurable(capabilities: WorkflowCapabilities | null, type: string): boolean {
  return capabilities?.nodes.some(node => node.type === type
    && (node.available === true || node.preparation_available === true)) === true;
}
