/**
 * Device Control Types
 * Types for device control functionality
 */

export type DeviceType = 'desktop' | 'mobile';
export type DeviceStatus = 'online' | 'offline';
export type DeviceScenarioId = 'files' | 'report';

/** A saved draft records intent. It does not authorize device execution. */
export interface DeviceScenarioDraft {
  device_id: string;
  project_id: string;
  scenario: DeviceScenarioId;
  purpose: string;
  business_system: string | null;
  target_directory: string | null;
  revision: number;
  status: 'draft';
  updated_at: string;
}

export interface DeviceScenarioDraftWrite {
  expected_revision: number;
  purpose: string;
  business_system: string | null;
  target_directory: string | null;
}

export interface DeviceReadPolicy {
  device_id: string;
  project_id: string;
  revision: number;
  tool_name: 'fs_read';
  allowed_directory: string;
  max_lines: number;
  max_offset: number;
  digest: string;
  status: 'active' | 'pending' | 'revoked';
  verified: boolean;
  last_tested_at: string | null;
  last_test_status: 'passed' | 'failed' | null;
  updated_at: string;
  revoked_at?: string | null;
}

export interface DeviceReadPolicyPublish {
  expected_draft_revision: number;
  expected_policy_revision: number;
  max_lines: number;
  max_offset: number;
}

export interface DeviceReadPolicyTest {
  path: string;
  expected_text: string;
  limit: number;
}

/**
 * Device information
 */
export interface Device {
  id: string;
  project_id: string;
  device_type: DeviceType;
  device_name: string;
  os: string;
  os_version?: string;
  screen_resolution?: string;
  status: DeviceStatus;
  last_seen_at?: string;
  created_at: string;
}

/**
 * Device list response
 */
export interface DeviceListResponse {
  devices: Device[];
  total: number;
}

/**
 * Bind code response
 */
export interface BindCodeResponse {
  bind_code: string;
  expires_at: string;
}

/**
 * Device update request
 */
export interface DeviceUpdateRequest {
  device_name?: string;
}

/**
 * Device list query parameters
 * Note: project_id is not needed as it's obtained from JWT token
 */
export interface DeviceListParams {
  device_type?: DeviceType;
  status?: DeviceStatus;
  skip?: number;
  limit?: number;
}

/**
 * Device control tool definition (for AI agent integration)
 */
export interface DeviceControlTool {
  id: string;
  name: string;
  description: string;
  device_id?: string;
  enabled: boolean;
}

/**
 * Device session information
 */
export interface DeviceSession {
  id: string;
  device_id: string;
  device_name: string;
  agent_id: string | null;
  agent_name: string | null;
  workflow_execution_id: string | null;
  status: DeviceSessionStatus;
  started_at: string;
  ended_at: string | null;
  lease_expires_at: string | null;
  screenshots_count: number;
  actions_count: number;
  failed_actions_count: number;
}

export type DeviceSessionStatus = 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export interface DeviceSessionStep {
  id: string;
  tool_name: string;
  status: Exclude<DeviceSessionStatus, 'cancelled'>;
  started_at: string;
  ended_at: string | null;
}
export interface DeviceSessionDetail extends DeviceSession {
  steps: DeviceSessionStep[];
  step_total: number;
}
export interface DeviceSessionListResponse {
  sessions: DeviceSession[];
  total: number;
}
export interface DeviceSessionListParams {
  device_id?: string;
  workflow_execution_id?: string;
  skip?: number;
  limit?: number;
}
export interface DeviceSessionDetailParams {
  step_skip?: number;
  step_limit?: number;
}
