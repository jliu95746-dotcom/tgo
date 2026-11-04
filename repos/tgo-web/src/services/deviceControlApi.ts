/**
 * Device Control API Service
 * API functions for device control management
 *
 * Note: project_id is automatically obtained from JWT token by the backend proxy,
 * so it doesn't need to be passed explicitly.
 */

import apiClient from './api';
import type { ScriptCatalog, ScriptLocalStatusRead, ScriptPolicyPublish, ScriptPolicyRead } from '@/types/deviceScriptPolicy';
import type { DeviceScreenPolicy, ScreenLocalGrant, ScreenPolicyPublish } from '@/types/deviceScreenPolicy';
import type { DeviceToolDescription } from '@/types/businessScenarios';
import type {
  Device,
  DeviceListResponse,
  BindCodeResponse,
  DeviceUpdateRequest,
  DeviceSessionListResponse,
  DeviceSessionDetail,
  DeviceSessionListParams,
  DeviceSessionDetailParams,
  DeviceScenarioDraft,
  DeviceScenarioDraftWrite,
  DeviceScenarioId,
  DeviceReadPolicy,
  DeviceReadPolicyPublish,
  DeviceReadPolicyTest,
} from '@/types/deviceControl';

const DEVICE_CONTROL_BASE_URL = '/v1/device-control';

export function getDeviceScriptCatalog(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<ScriptCatalog> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/script-catalog`, options);
}

export function getDeviceScriptStatus(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<ScriptLocalStatusRead> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/script-status`, options);
}

export function getDeviceScriptPolicy(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<ScriptPolicyRead> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/script-policy`, options);
}

export function publishDeviceScriptPolicy(deviceId: string, data: ScriptPolicyPublish): Promise<ScriptPolicyRead> {
  return apiClient.put(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/script-policy`, data);
}

export function revokeDeviceScriptPolicy(deviceId: string, expectedRevision: number): Promise<ScriptPolicyRead> {
  return apiClient.post(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/script-policy/revoke`, { expected_policy_revision: expectedRevision });
}

export function getDeviceScreenGrant(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<ScreenLocalGrant> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/screen-grant`, options);
}
export function getDeviceScreenPolicy(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<DeviceScreenPolicy> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/screen-policy`, options);
}
export function publishDeviceScreenPolicy(deviceId: string, data: ScreenPolicyPublish): Promise<DeviceScreenPolicy> {
  return apiClient.put(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/screen-policy`, data);
}
export function revokeDeviceScreenPolicy(deviceId: string, expectedRevision: number): Promise<DeviceScreenPolicy> {
  return apiClient.post(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/screen-policy/revoke`, { expected_policy_revision: expectedRevision });
}
export function revokeDeviceReadPolicy(deviceId: string, expectedRevision: number): Promise<DeviceReadPolicy> {
  return apiClient.post(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/read-policy/revoke`, { expected_policy_revision: expectedRevision });
}

export function getDeviceReadPolicy(
  deviceId: string, options: { signal?: AbortSignal } = {},
): Promise<DeviceReadPolicy> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/read-policy`, options);
}

export function publishDeviceReadPolicy(
  deviceId: string, data: DeviceReadPolicyPublish,
): Promise<DeviceReadPolicy> {
  return apiClient.put(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/read-policy`, data);
}

export function testDeviceReadPolicy(
  deviceId: string, data: DeviceReadPolicyTest,
): Promise<DeviceReadPolicy> {
  return apiClient.post(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/read-policy/test`, data);
}

export function getDeviceScenarioDraft(
  deviceId: string, scenario: DeviceScenarioId, options: { signal?: AbortSignal } = {},
): Promise<DeviceScenarioDraft> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/scenario-drafts/${scenario}`, options);
}

export function saveDeviceScenarioDraft(
  deviceId: string, scenario: DeviceScenarioId, data: DeviceScenarioDraftWrite,
): Promise<DeviceScenarioDraft> {
  return apiClient.put(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/scenario-drafts/${scenario}`, data);
}

export function getDeviceTools(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<{ tools: DeviceToolDescription[] }> {
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}/tools`, options);
}

export function listDeviceSessions(
  params: DeviceSessionListParams = {},
  options: { signal?: AbortSignal } = {},
): Promise<DeviceSessionListResponse> {
  const query = new URLSearchParams();
  if (params.device_id) query.set('device_id', params.device_id);
  if (params.workflow_execution_id) query.set('workflow_execution_id', params.workflow_execution_id);
  query.set('skip', String(params.skip ?? 0));
  query.set('limit', String(params.limit ?? 20));
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/sessions?${query}`, options);
}

export function getDeviceSession(
  sessionId: string,
  params: DeviceSessionDetailParams = {},
  options: { signal?: AbortSignal } = {},
): Promise<DeviceSessionDetail> {
  const query = new URLSearchParams({
    step_skip: String(params.step_skip ?? 0), step_limit: String(params.step_limit ?? 100),
  });
  return apiClient.get(`${DEVICE_CONTROL_BASE_URL}/sessions/${encodeURIComponent(sessionId)}?${query}`, options);
}

export interface ListDevicesParams {
  device_type?: string;
  status?: string;
  skip?: number;
  limit?: number;
}

/**
 * List all devices for the current project
 */
export async function listDevices(
  params?: ListDevicesParams,
  options: { signal?: AbortSignal } = {},
): Promise<DeviceListResponse> {
  const queryParams = new URLSearchParams();
  if (params?.device_type) queryParams.append('device_type', params.device_type);
  if (params?.status) queryParams.append('status', params.status);
  if (params?.skip !== undefined) queryParams.append('skip', params.skip.toString());
  if (params?.limit !== undefined) queryParams.append('limit', params.limit.toString());

  const query = queryParams.toString();
  const url = query
    ? `${DEVICE_CONTROL_BASE_URL}/devices?${query}`
    : `${DEVICE_CONTROL_BASE_URL}/devices`;

  return apiClient.get<DeviceListResponse>(url, options);
}

/**
 * Get a specific device by ID
 */
export async function getDevice(deviceId: string, options: { signal?: AbortSignal } = {}): Promise<Device> {
  return apiClient.get<Device>(`${DEVICE_CONTROL_BASE_URL}/devices/${encodeURIComponent(deviceId)}`, options);
}

/**
 * Generate a new bind code for device registration
 */
export async function generateBindCode(): Promise<BindCodeResponse> {
  return apiClient.post<BindCodeResponse>(`${DEVICE_CONTROL_BASE_URL}/devices/bind-code`);
}

/**
 * Update a device
 */
export async function updateDevice(
  deviceId: string,
  data: DeviceUpdateRequest
): Promise<Device> {
  return apiClient.patch<Device>(
    `${DEVICE_CONTROL_BASE_URL}/devices/${deviceId}`,
    data
  );
}

/**
 * Delete (unbind) a device
 */
export async function deleteDevice(deviceId: string): Promise<void> {
  await apiClient.delete(`${DEVICE_CONTROL_BASE_URL}/devices/${deviceId}`);
}

/**
 * Force disconnect a device
 */
export async function disconnectDevice(deviceId: string): Promise<void> {
  await apiClient.post(`${DEVICE_CONTROL_BASE_URL}/devices/${deviceId}/disconnect`);
}

export default {
  listDeviceSessions,
  getDeviceSession,
  listDevices,
  getDevice,
  generateBindCode,
  updateDevice,
  deleteDevice,
  disconnectDevice,
};
