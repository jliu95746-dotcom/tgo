import type { AgentToolUnion } from '@/types';

export interface ToolDetailView {
  name: string;
  description?: string | null;
  input_schema?: Record<string, unknown> | null;
}

export interface SchemaParameterView {
  name: string;
  type?: string;
  description?: string;
  required: boolean;
}

const textValue = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value : undefined;

const objectValue = (value: unknown): Record<string, unknown> | undefined =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;

// Allowlist the public description/schema; never render tool credentials/config.
export function agentToolDetails(tool: AgentToolUnion): ToolDetailView {
  return {
    name: ('title' in tool ? textValue(tool.title) : undefined)
      || ('name' in tool ? textValue(tool.name) : undefined)
      || textValue(tool.tool_name) || tool.id,
    description: 'description' in tool ? textValue(tool.description) : undefined,
    input_schema: 'input_schema' in tool ? objectValue(tool.input_schema) : undefined,
  };
}

export function schemaParameters(schema?: Record<string, unknown> | null): SchemaParameterView[] {
  const properties = objectValue(schema?.properties);
  if (!properties) return [];
  const required = Array.isArray(schema?.required) ? schema.required : [];
  return Object.entries(properties).map(([name, raw]) => {
    const parameter = objectValue(raw);
    const type = Array.isArray(parameter?.type)
      ? parameter.type.filter(item => typeof item === 'string').join(' | ')
      : textValue(parameter?.type);
    return { name, type: type || undefined, description: textValue(parameter?.description),
      required: required.includes(name) };
  });
}
