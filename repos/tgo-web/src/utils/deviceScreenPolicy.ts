import type { DeviceReadPolicy } from '@/types/deviceControl';
import type { DeviceScreenPolicy, ScreenLocalGrant, ScreenPolicyPublish, ScreenRegion } from '@/types/deviceScreenPolicy';

export function ownsDevicePolicy(value: { device_id: string; project_id: string } | null, deviceId: string, projectId: string): boolean {
  return Boolean(value && value.device_id === deviceId && value.project_id === projectId);
}
interface ScreenPolicyInput {
  deviceId: string; projectId: string; local: ScreenLocalGrant | null;
  file: DeviceReadPolicy | null; screen: DeviceScreenPolicy | null;
  maxActions: number; allowClick: boolean; allowText: boolean; regions: ScreenRegion[];
}
function finiteRegion(region: ScreenRegion): boolean {
  return [region.x, region.y, region.width, region.height].every(Number.isInteger)
    && region.x >= 0 && region.y >= 0 && region.width > 0 && region.height > 0
    && region.x + region.width <= 8192 && region.y + region.height <= 8192;
}
export function prepareScreenPolicy(input: ScreenPolicyInput): ScreenPolicyPublish | null {
  const { deviceId, projectId, local, file, screen, maxActions, allowClick, allowText, regions } = input;
  if (!local || !ownsDevicePolicy(local, deviceId, projectId)
    || (file && (!ownsDevicePolicy(file, deviceId, projectId) || file.status !== 'revoked'))
    || (screen && !ownsDevicePolicy(screen, deviceId, projectId))) return null;
  const grant = local.grant;
  if (!grant.active || grant.protocol_version !== 1 || !grant.grant_id || !/^[0-9a-f]{32}$/.test(grant.grant_id)
    || !local.connection_instance_id || !/^[0-9a-f]{32}$/.test(local.connection_instance_id)
    || !Number.isInteger(maxActions) || maxActions < 1 || maxActions > 50
    || maxActions > grant.max_actions || maxActions <= grant.used_actions
    || (allowClick && !grant.allow_click) || (allowText && !grant.allow_text)
    || allowClick !== (regions.length > 0) || regions.length > 16) return null;
  if (regions.some(region => !finiteRegion(region) || !grant.click_regions.some(parent => finiteRegion(parent)
    && region.x >= parent.x && region.y >= parent.y
    && region.x + region.width <= parent.x + parent.width
    && region.y + region.height <= parent.y + parent.height))) return null;
  return {
    expected_policy_revision: screen?.policy.revision ?? 0,
    connection_instance_id: local.connection_instance_id, grant_id: grant.grant_id,
    max_actions: maxActions, allow_click: allowClick, allow_text: allowText,
    click_regions: regions.map(region => ({ ...region })),
  };
}
