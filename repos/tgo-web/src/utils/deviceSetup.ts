import type { DeviceToolDescription } from '@/types/businessScenarios';
import type { DeviceScenarioId } from '@/types/deviceSetup';
import type { DeviceReadPolicy } from '@/types/deviceControl';

/** A tool listing is a capability check, never an execution/test receipt. */
export function supportsDeviceScenario(scenario: DeviceScenarioId, tools: DeviceToolDescription[]): boolean {
  if (!Array.isArray(tools)) return false;
  // Reviewed reports require their own policy and business receipt, not MCP.
  const required = scenario === 'files' ? 'fs_read' : null;
  return required !== null && tools.some(tool => tool && tool.name === required);
}

/** Require a fresh server response for the selected device before setup. */
export function isVerifiedDeviceReadPolicy(
  policy: DeviceReadPolicy | null,
  deviceId: string,
  projectId: string,
): boolean {
  return Boolean(policy && policy.device_id === deviceId && policy.project_id === projectId
    && policy.tool_name === 'fs_read' && policy.status === 'active' && policy.verified);
}
