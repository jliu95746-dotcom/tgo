export type ScenarioModule = 'agents' | 'workflows' | 'devices';
export type ScenarioId = 'knowledge' | 'logistics' | 'aftersales' | 'assistant' | 'files' | 'report' | 'screen';
export interface BusinessScenario {
  id: ScenarioId;
  module: ScenarioModule;
  requirements: string[];
  available: boolean;
}
export interface ScenarioWorkflowOptions {
  name: string;
  agentId?: string;
  connectionId?: string;
  review?: {
    approverStaffId: string;
    caseCategory: 'return_request' | 'exchange_request' | 'quality_issue';
  };
}
export interface DeviceToolDescription {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}
