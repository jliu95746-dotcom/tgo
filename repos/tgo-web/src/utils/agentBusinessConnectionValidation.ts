import type { AgentBusinessConnection, AgentBusinessConnections } from '@/types/agentBusinessConnections';

const fields = new Set(['scope','can_enable','id','project_id','agent_id','connection_id','operations','enabled',
  'revision','configuration_revision','authorization_revision','resource_revision','configured_by']);
export function validAgentBusinessBinding(row: AgentBusinessConnection, project: string, agent: string): boolean {
  return Boolean(row && Object.keys(row).every(key=>fields.has(key)) && row.project_id===project && row.agent_id===agent &&
    row.scope==='employee_business_configuration' && row.can_enable===false &&
    [row.id,row.connection_id,row.configured_by].every(value=>typeof value==='string'&&value.length>0) &&
    typeof row.enabled==='boolean' && [row.revision,row.configuration_revision,row.authorization_revision].every(value=>Number.isInteger(value)&&value>=1) &&
    /^[0-9a-f]{64}$/.test(row.resource_revision) && Array.isArray(row.operations) && row.operations.length>0 && row.operations.length<=4 &&
    new Set(row.operations).size===row.operations.length && row.operations.every(value=>['order_query','logistics_query','customer_profile_query','membership_query'].includes(value)));
}
export function validAgentBusinessConfiguration(value: AgentBusinessConnections, project: string, agent: string): boolean {
  return Boolean(value && Object.keys(value).every(key=>['scope','can_enable','project_id','agent_id','bindings'].includes(key)) &&
    value.project_id===project && value.agent_id===agent && value.scope==='employee_business_configuration' && value.can_enable===false &&
    Array.isArray(value.bindings) && value.bindings.every(row=>validAgentBusinessBinding(row,project,agent)) &&
    new Set(value.bindings.map(row=>row.connection_id)).size===value.bindings.length);
}
