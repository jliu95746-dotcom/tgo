import type { BusinessCapability } from './businessConnections';

export interface AgentBusinessConnection {
  scope: 'employee_business_configuration'; can_enable: false;
  id: string; project_id: string; agent_id: string; connection_id: string;
  operations: BusinessCapability[]; enabled: boolean; revision: number;
  configuration_revision: number; authorization_revision: number; resource_revision: string;
  configured_by: string;
}
export interface AgentBusinessConnections {
  scope: 'employee_business_configuration'; can_enable: false;
  project_id: string; agent_id: string; bindings: AgentBusinessConnection[];
}
export interface AgentBusinessConnectionWrite {
  request_id: string; expected_revision: number; operations: BusinessCapability[]; enabled: boolean;
}
