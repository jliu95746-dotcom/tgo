import type { CreateAgentFormData } from '@/types';
import type { ScenarioDraft, ScenarioDraftInput, ScenarioStep, ScenarioTemplate } from '@/types/scenarioDrafts';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';

export const AGENT_SETUP_STEPS: ScenarioStep[] = ['scenario', 'business', 'resources', 'testing', 'activation'];

export function initialRoleSettings(role: ScenarioTemplate['id'], form: Pick<CreateAgentFormData, 'scenarioId' | 'skills_enabled'>): Partial<CreateAgentFormData> {
  return !form.scenarioId && ['knowledge', 'aftersales'].includes(role) ? { skills_enabled: false } : {};
}

/** Navigation validates editable fields; a saved draft is never proof of a successful test. */
export function agentStepProblem(step: ScenarioStep, form: CreateAgentFormData): string | null {
  if (step === 'scenario' && !form.scenarioId) return 'chooseRole';
  if (step === 'business') {
    if (!form.name.trim() || !form.profession.trim() || !form.description.trim()) return 'completeBusiness';
    if (!form.companySummary?.trim()) return 'completeCompany';
  }
  if (step === 'resources' && (!form.llmModel || (form.llmModel !== SYSTEM_DEFAULT_MODEL && form.llmModel.indexOf(':') < 1))) return 'chooseModel';
  return null;
}

export function agentDraftInput(form: CreateAgentFormData, template: ScenarioTemplate, channel: ScenarioDraft['channel'], step: ScenarioStep, current: ScenarioDraft | null): ScenarioDraftInput {
  if (Object.values(form.toolConfigs).some(config => Object.keys(config).length > 0)) throw new Error('toolConfig');
  const boundary = form.llmModel.indexOf(':');
  if (form.llmModel && form.llmModel !== SYSTEM_DEFAULT_MODEL && boundary < 1) throw new Error('invalidModel');
  return {
    template_id: template.id, module: 'agents', template_version: current?.template_version ?? template.version, channel, step,
    business: { name: form.name, profession: form.profession, company_summary: form.companySummary ?? '', instructions: form.description },
    bindings: {
      model: boundary > 0 ? { provider_id: form.llmModel.slice(0, boundary), model_id: form.llmModel.slice(boundary + 1) } : null,
      knowledge_ids: form.knowledgeBases, tool_ids: form.tools, workflow_ids: form.workflows,
      device_ids: form.boundDeviceId ? [form.boundDeviceId] : [], connection_ids: current?.bindings.connection_ids ?? [],
    },
    agent_settings: {
      markdown: form.markdown ?? true, add_datetime_to_context: form.add_datetime_to_context ?? true,
      skills_enabled: form.skills_enabled ?? true, show_tool_calls: form.show_tool_calls ?? false,
      tool_call_limit: form.tool_call_limit ?? 10, num_history_runs: form.num_history_runs ?? 5,
      humanization_skill_name: form.humanization_skill_name ?? null, humanization_skill_enabled: form.humanization_skill_enabled ?? false,
    },
  };
}

export function formFromAgentDraft(draft: ScenarioDraft): Partial<CreateAgentFormData> {
  return {
    scenarioId: draft.template_id, name: draft.business.name, profession: draft.business.profession,
    companySummary: draft.business.company_summary, description: draft.business.instructions,
    llmModel: draft.bindings.model ? `${draft.bindings.model.provider_id}:${draft.bindings.model.model_id}` : SYSTEM_DEFAULT_MODEL,
    knowledgeBases: draft.bindings.knowledge_ids, tools: draft.bindings.tool_ids, workflows: draft.bindings.workflow_ids,
    boundDeviceId: draft.bindings.device_ids[0] ?? '', toolConfigs: {}, ...draft.agent_settings,
  };
}
