import type { CreateAgentFormData } from '@/types';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';
import { useAuthStore } from '@/stores/authStore';
import { useScenarioDraftStore } from '@/stores/scenarioDraftStore';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';
import { agentDraftInput } from './agentScenarioForm';

export function shouldExpandAgentResources(form: Pick<CreateAgentFormData, 'scenarioId' | 'llmModel' | 'tools' | 'workflows' | 'boundDeviceId'>): boolean {
  return form.scenarioId === 'logistics' || form.scenarioId === 'assistant' || form.tools.length > 0 || form.workflows.length > 0
    || Boolean(form.boundDeviceId) || Boolean(form.llmModel && form.llmModel !== SYSTEM_DEFAULT_MODEL);
}

/** Only a confirmed save in the original staff session can allow the caller to close the form. */
export async function saveAgentSetupDraft(form: CreateAgentFormData): Promise<boolean> {
  const owner = useAuthStore.getState().user;
  const store = useScenarioDraftStore.getState();
  const template = store.templates.find(value => value.module === 'agents' && value.id === form.scenarioId);
  if (owner?.role !== 'admin' || store.busy || !template) return false;
  const channel = useScenarioResourceStore.getState().channel;
  const saved = await store.save(agentDraftInput(form, template, channel, store.step, store.current));
  const current = useAuthStore.getState().user;
  return saved && current?.id === owner.id && current.project_id === owner.project_id && current.role === owner.role;
}
