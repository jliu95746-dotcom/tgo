import type { AIModelWithProviderDTO } from '@/services/aiProvidersApi';
import type { Agent, AgentWithDetailsResponse } from '@/types';

/** The list reflects persisted toggles sooner than the separate default summary. */
export function isDefaultAgentEnabled(
  defaultAgent: Pick<AgentWithDetailsResponse, 'id' | 'is_active'> | null,
  agents: readonly Pick<Agent, 'id' | 'status'>[],
): boolean {
  if (!defaultAgent) return false;
  const current = agents.find(agent => agent.id === defaultAgent.id);
  return current ? current.status === 'active' : defaultAgent.is_active !== false;
}

export type AgentModelConfiguration = Pick<AIModelWithProviderDTO,
  'provider_id' | 'model_id' | 'model_type' | 'is_active'>;
export type AgentReadiness = 'checking' | 'check_failed' | 'missing_model'
  | 'unavailable_model' | 'provider_unassigned' | 'configured' | 'following_system';

/** Configuration evidence only: matching a model never proves a successful run. */
export function getAgentReadiness(
  model: string | undefined,
  models: readonly AgentModelConfiguration[],
  state: 'idle' | 'loading' | 'loaded' | 'error',
): AgentReadiness {
  if (!model?.trim()) return 'missing_model';
  if (model === '__system_default__') return 'following_system';
  if (state === 'error') return 'check_failed';
  if (state !== 'loaded') return 'checking';
  const separator = model.indexOf(':');
  const provider = separator >= 0 ? model.slice(0, separator) : '';
  const name = separator >= 0 ? model.slice(separator + 1) : model;
  const candidates = models.filter(item => item.is_active && item.model_type === 'chat'
    && item.model_id === name && (!provider || item.provider_id === provider));
  if (!candidates.length) return 'unavailable_model';
  return provider ? 'configured' : 'provider_unassigned';
}
