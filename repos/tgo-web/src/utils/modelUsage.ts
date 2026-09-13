import type { ModelType } from '@/services/aiProvidersApi';
import type { ProjectAIConfigUpdate } from '@/services/projectConfigApi';

export const modelUsageTypes: ModelType[] = ['chat', 'embedding', 'asr', 'ocr', 'vlm'];
export type ModelUsageSelections = Record<ModelType, string>;

export function toggleModelUsage(current: ModelUsageSelections, type: ModelType, value: string): ModelUsageSelections {
  return { ...current, [type]: current[type] === value ? '' : value };
}

export function changedModelUsage(saved: ModelUsageSelections, selected: ModelUsageSelections): ProjectAIConfigUpdate {
  const patch: ProjectAIConfigUpdate = {};
  for (const type of modelUsageTypes) {
    if (saved[type] === selected[type]) continue;
    const value = selected[type];
    const separator = value.indexOf(':');
    patch[`default_${type}_provider_id`] = separator > 0 ? value.slice(0, separator) : null;
    patch[`default_${type}_model`] = separator > 0 ? value.slice(separator + 1) : null;
  }
  return patch;
}
