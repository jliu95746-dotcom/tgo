import type { ModelProviderConfig, AIModelConfig, ProviderKind } from '@/stores/providersStore';
import AIProvidersApiService, { type ProviderConnectionDraft } from '@/services/aiProvidersApi';
export const providerPresets: { kind: ProviderKind; label: string; base: string }[] = [
  { kind: 'deepseek', label: 'DeepSeek', base: 'https://api.deepseek.com/v1' },
  { kind: 'qwen', label: '通义千问', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { kind: 'openai', label: 'OpenAI', base: 'https://api.openai.com/v1' },
  { kind: 'moonshot', label: 'Kimi', base: 'https://api.moonshot.cn/v1' },
  { kind: 'azure', label: 'Azure OpenAI', base: '' },
  { kind: 'baichuan', label: '百川智能', base: 'https://api.baichuan-ai.com/v1' },
  { kind: 'ollama', label: 'Ollama', base: 'http://localhost:11434' },
  { kind: 'custom', label: '其他兼容接口', base: '' },
];
export function existingModelConfigs(provider: ModelProviderConfig): AIModelConfig[] {
  return (provider.models || []).map(id => provider.modelConfigs?.find(model => model.id === id) || { id, name: id, type: provider.modelTypes?.[id] || 'chat' });
}
export function connectionDraft(provider: Partial<ModelProviderConfig> & { kind: ProviderKind }): ProviderConnectionDraft {
  return { provider_id: provider.id || undefined, provider: AIProvidersApiService.kindToProviderKey(provider.kind),
    api_base_url: provider.apiBaseUrl || '', ...(provider.apiKey?.trim() ? { api_key: provider.apiKey.trim() } : {}),
    config: AIProvidersApiService.buildBackendConfig(provider.kind, provider.params) };
}
// Never reset blank keys, activation state, or legacy defaults during editing.
export function providerEditPatch(original: ModelProviderConfig, draft: ModelProviderConfig, models: AIModelConfig[]): Partial<ModelProviderConfig> {
  const patch: Partial<ModelProviderConfig> = {};
  if (draft.name.trim() !== original.name) patch.name = draft.name.trim();
  if ((draft.apiBaseUrl || '').trim() !== (original.apiBaseUrl || '')) patch.apiBaseUrl = draft.apiBaseUrl?.trim();
  if (draft.apiKey.trim()) patch.apiKey = draft.apiKey.trim();
  if (JSON.stringify(draft.params) !== JSON.stringify(original.params)) patch.params = draft.params;
  if (JSON.stringify(models) !== JSON.stringify(existingModelConfigs(original))) {
    patch.models = models.map(model => model.id); patch.modelConfigs = models;
    patch.modelTypes = Object.fromEntries(models.map(model => [model.id, model.type]));
  }
  return patch;
}
