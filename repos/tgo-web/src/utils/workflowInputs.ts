import type { WorkflowInputVariable } from '@/types/workflow';

export interface WorkflowInputIssue {
  field: string;
  code: 'required' | 'type' | 'length' | 'range' | 'choice';
}

export function workflowInputIssues(
  variables: WorkflowInputVariable[], input: Record<string, unknown>,
): WorkflowInputIssue[] {
  const issues: WorkflowInputIssue[] = [];
  for (const variable of variables) {
    const value = input[variable.name];
    const missing = value == null || (typeof value === 'string' && !value.trim());
    if (variable.required && missing) {
      issues.push({ field: variable.name, code: 'required' });
      continue;
    }
    if (value == null) continue;
    const valid = variable.type === 'number'
      ? typeof value === 'number' && Number.isFinite(value)
      : typeof value === variable.type;
    if (!valid) {
      issues.push({ field: variable.name, code: 'type' });
      continue;
    }
    if (typeof value === 'string') {
      const length = [...value].length;
      if ((variable.min_length != null && length < variable.min_length)
        || (variable.max_length != null && length > variable.max_length)) {
        issues.push({ field: variable.name, code: 'length' });
      }
      if (variable.allowed_values != null && !variable.allowed_values.includes(value)) {
        issues.push({ field: variable.name, code: 'choice' });
      }
    }
    if (typeof value === 'number'
      && ((variable.minimum != null && value < variable.minimum)
        || (variable.maximum != null && value > variable.maximum))) {
      issues.push({ field: variable.name, code: 'range' });
    }
  }
  return issues;
}

export function parseWorkflowInput(value: string, type: WorkflowInputVariable['type']): string | number | boolean | undefined {
  if (type === 'number') return value.trim() === '' ? undefined : Number(value);
  if (type === 'boolean') return value === 'true' ? true : value === 'false' ? false : undefined;
  return value;
}
