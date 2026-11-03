import apiClient from './api';
import type { KnowledgeChannel, ScenarioResourceCatalog, ScenarioReadinessReport, ScenarioReadinessRequest } from '@/types/scenarioResources';
import type { ScenarioModule } from '@/types/businessScenarios';
import type { ScenarioAgentReceipt, ScenarioDraft, ScenarioDraftCreate, ScenarioDraftUpdate, ScenarioTemplate } from '@/types/scenarioDrafts';

export const BusinessScenariosApi = {
  saveDraftAgent: (id: string, revision: number, signal?: AbortSignal) => apiClient.post<ScenarioAgentReceipt>(`/v1/business-scenarios/drafts/${encodeURIComponent(id)}/agent`, { expected_revision: revision }, { signal }),
  draftAgent: (id: string) => apiClient.get<ScenarioAgentReceipt>(`/v1/business-scenarios/drafts/${encodeURIComponent(id)}/agent`),
  resources: (channel: KnowledgeChannel) => apiClient.get<ScenarioResourceCatalog>(`/v1/business-scenarios/resources?channel=${channel}`),
  readiness: (request: ScenarioReadinessRequest) => apiClient.post<ScenarioReadinessReport>('/v1/business-scenarios/readiness', request),
  templates: (module: ScenarioModule, signal?: AbortSignal) => apiClient.get<ScenarioTemplate[]>(`/v1/business-scenarios/templates?module=${module}`, { signal }),
  drafts: (module: ScenarioModule) => apiClient.get<ScenarioDraft[]>(`/v1/business-scenarios/drafts?module=${module}&limit=100`),
  draft: (id: string) => apiClient.get<ScenarioDraft>(`/v1/business-scenarios/drafts/${encodeURIComponent(id)}`),
  createDraft: (request: ScenarioDraftCreate) => apiClient.post<ScenarioDraft>('/v1/business-scenarios/drafts', request),
  updateDraft: (id: string, request: ScenarioDraftUpdate) => apiClient.patch<ScenarioDraft>(`/v1/business-scenarios/drafts/${encodeURIComponent(id)}`, request),
};
