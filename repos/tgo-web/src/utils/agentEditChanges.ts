import type { Agent } from '@/types';
import type { AgentFormState } from '@/hooks/useAgentForm';

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, canonical(item)]));
  return value;
}
const same = (left: unknown, right: unknown) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

/** Preserve server settings and send only fields the employee editor changed. */
export function agentEditChanges(form: AgentFormState, original: AgentFormState, originalConfig: Agent['config'] = {}): Partial<Agent> {
  const changes: Partial<Agent> = {};
  if (!same(form.name, original.name)) changes.name = form.name;
  if (!same(form.description, original.description)) changes.description = form.description;
  if (!same(form.profession, original.profession)) changes.role = form.profession;
  if (!same(form.llmModel, original.llmModel)) changes.llmModel = form.llmModel;
  if (!same(form.tools, original.tools)) changes.tools = form.tools;
  if (!same(form.toolConfigs, original.toolConfigs)) changes.toolConfigs = form.toolConfigs;
  if (!same(form.knowledgeBases, original.knowledgeBases)) changes.knowledgeBases = form.knowledgeBases;
  if (!same(form.workflows, original.workflows)) changes.workflows = form.workflows;
  if (!same(form.boundDeviceId, original.boundDeviceId)) changes.boundDeviceId = form.boundDeviceId;
  if (!same(form.skills_enabled, original.skills_enabled)) changes.skills_enabled = form.skills_enabled;
  if (!same(form.humanization_skill_name, original.humanization_skill_name)) changes.humanization_skill_name = form.humanization_skill_name || null;
  if (!same(form.humanization_skill_enabled, original.humanization_skill_enabled)) changes.humanization_skill_enabled = form.humanization_skill_enabled ?? false;
  const config = { ...originalConfig };
  let configChanged = false;
  for (const key of ['markdown', 'add_datetime_to_context', 'tool_call_limit', 'num_history_runs'] as const) {
    if (!same(form[key], original[key])) { Object.assign(config, { [key]: form[key] }); configChanged = true; }
  }
  if (configChanged) changes.config = config;
  return changes;
}
