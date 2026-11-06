import type { WorkflowExecution, WorkflowJsonValue, WorkflowReconciliationSummary } from '@/types/workflow';

export function reconciliationOutput(value: WorkflowJsonValue | undefined): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object' && !Array.isArray(value) && 'result' in value) {
    return reconciliationOutput(value.result);
  }
  return JSON.stringify(value, null, 2);
}

export function reconciliationDetailsMatch(
  review: WorkflowReconciliationSummary, execution: WorkflowExecution | null,
): boolean {
  return execution?.id === review.execution_id
    && execution.workflow_id === review.workflow_id
    && Array.isArray(execution.node_executions)
    && review.nodes.length > 0
    && review.nodes.every(node => execution.node_executions.some(
      receipt => receipt.id === node.id && receipt.node_id === node.node_id
        && receipt.status === node.status,
    ));
}
