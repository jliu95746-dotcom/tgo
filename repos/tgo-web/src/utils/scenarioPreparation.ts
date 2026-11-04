import type { ScenarioTemplate } from '@/types/scenarioDrafts';
import type { CreateAgentFormData } from '@/types';
import type { ResourceKind, ScenarioBindings, ScenarioReadinessReport, ScenarioResourceCatalog } from '@/types/scenarioResources';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';

const resourceFields = {
  knowledge: 'knowledge_ids', tool: 'tool_ids', workflow: 'workflow_ids', connection: 'connection_ids', device: 'device_ids',
} as const;
const resourceRoutes: Record<ResourceKind, string> = {
  model: '/settings', knowledge: '/knowledge', tool: '/ai/tools', workflow: '/ai/workflows', connection: '/ai/tools', device: '/ai/device-control',
};

/** Suggestions are selections for a fresh owner check; they never grant execution. */
export function suggestScenarioBindings(template: ScenarioTemplate, catalog: ScenarioResourceCatalog): ScenarioBindings {
  const result: ScenarioBindings = { model: null, knowledge_ids: [], tool_ids: [], workflow_ids: [], connection_ids: [], device_ids: [] };
  const required = new Set(template.required_resources);
  if (template.id === 'assistant') required.add('knowledge');
  for (const kind of required) {
    const section = catalog.sections.find(value => value.kind === kind);
    if (section?.status !== 'available') continue;
    const candidates = section.items.filter(value => value.kind === kind && value.status === 'configured');
    if (kind === 'model') {
      const models = candidates.filter(value => value.details.purpose === 'chat');
      if (models.some(value => value.details.is_default)) continue;
      if (models.length === 1 && models[0].details.provider_id && models[0].details.model_id) {
        result.model = { provider_id: models[0].details.provider_id, model_id: models[0].details.model_id };
      }
    } else if (candidates.length === 1) {
      result[resourceFields[kind]] = [candidates[0].id];
    }
  }
  return result;
}

export function isCurrentPreparationReport(report: ScenarioReadinessReport, template: ScenarioTemplate,
                                           catalog: ScenarioResourceCatalog, requested: ScenarioBindings): boolean {
  if (!report || report.project_id !== catalog.project_id || report.channel !== catalog.channel
      || report.template_id !== template.id || report.template_version !== template.version
      || typeof report.configuration_ready !== 'boolean' || report.can_enable !== false || report.test_status !== 'not_tested'
      || !Array.isArray(report.issues) || report.configuration_ready !== (report.issues.length === 0)
      || !report.effective_bindings) return false;
  if (!report.issues.every(issue => issue && Object.prototype.hasOwnProperty.call(resourceRoutes, issue.kind)
      && issue.route === resourceRoutes[issue.kind] && typeof issue.message === 'string'
      && ['retry', 'choose_resource', 'configure_resource'].includes(issue.action))) return false;
  for (const field of Object.values(resourceFields)) {
    const returned = report.effective_bindings[field];
    if (!Array.isArray(returned) || returned.some(id => typeof id !== 'string')
        || JSON.stringify(returned) !== JSON.stringify(requested[field])) return false;
  }
  const model = report.effective_bindings.model;
  if (model !== null && (!model || typeof model.provider_id !== 'string' || typeof model.model_id !== 'string')) return false;
  if (requested.model && (model?.provider_id !== requested.model.provider_id || model.model_id !== requested.model.model_id)) return false;
  return true;
}

export function preparedResourceKinds(template: ScenarioTemplate, report: ScenarioReadinessReport): ResourceKind[] {
  const blocked = new Set(report.issues.map(issue => issue.kind));
  return template.required_resources.filter(kind => !blocked.has(kind));
}

/** The persistent system default stays a mode, not a copy of today's provider. */
export function preparationAgentResources(report: ScenarioReadinessReport, catalog: ScenarioResourceCatalog): Pick<CreateAgentFormData, 'llmModel' | 'knowledgeBases' | 'tools' | 'workflows' | 'boundDeviceId'> {
  const effective = report.effective_bindings;
  const selected = effective.model;
  const followsSystem = selected && catalog.sections.find(section => section.kind === 'model')?.items.some(resource =>
    resource.details.is_default && resource.details.provider_id === selected.provider_id && resource.details.model_id === selected.model_id);
  return {
    llmModel: followsSystem || !selected ? SYSTEM_DEFAULT_MODEL : `${selected.provider_id}:${selected.model_id}`,
    knowledgeBases: [...effective.knowledge_ids], tools: [...effective.tool_ids], workflows: [...effective.workflow_ids],
    boundDeviceId: effective.device_ids[0] ?? '',
  };
}
