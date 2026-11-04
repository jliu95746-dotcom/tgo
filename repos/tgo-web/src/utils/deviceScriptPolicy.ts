import type { DeviceReadPolicy } from '@/types/deviceControl';
import type { DeviceScreenPolicy } from '@/types/deviceScreenPolicy';
import type { ScriptCatalog, ScriptLocalStatusRead, ScriptPolicyPublish, ScriptPolicyRead } from '@/types/deviceScriptPolicy';

export interface ScriptPolicyForm {
  scriptId: string;
  timeoutSeconds: number;
  maxOutputBytes: number;
  maxRuns: number;
}

interface PublicationScope extends ScriptPolicyForm {
  deviceId: string;
  projectId: string;
  local: ScriptLocalStatusRead | null;
  catalog: ScriptCatalog | null;
  policy: ScriptPolicyRead | null;
  file: DeviceReadPolicy | null;
  screen: DeviceScreenPolicy | null;
}

export function ownsScriptScope(value: { device_id: string; project_id: string }, deviceId: string, projectId: string): boolean {
  return value.device_id === deviceId && value.project_id === projectId;
}

/** Preparing a policy never constitutes a business test or starts execution. */
export function prepareScriptPolicy(input: PublicationScope): ScriptPolicyPublish | null {
  const { local, catalog, policy, file, screen, deviceId, projectId } = input;
  if (!local || !ownsScriptScope(local, deviceId, projectId)
    || !/^[a-f0-9]{32}$/.test(local.connection_instance_id ?? '')
    || local.session.protocol_version !== 1
    || !['awaiting_policy', 'active'].includes(local.session.state)
    || !/^[a-f0-9]{32}$/.test(local.session.session_id ?? '')
    || !catalog || catalog.protocol_version !== 1 || !Array.isArray(catalog.scripts)) return null;
  if ([policy, file, screen].some(value => value && !ownsScriptScope(value, deviceId, projectId))) return null;
  if ([file, screen].some(value => value && value.status !== 'revoked')) return null;
  const item = catalog.scripts.find(script => script.script_id === input.scriptId);
  if (!item || item.effect !== 'read_only' || !/^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(item.script_id)
    || !/^[a-f0-9]{64}$/.test(item.grant_digest)) return null;
  const revision = policy?.policy.revision ?? 0;
  if (!Number.isInteger(revision) || revision < 0 || revision >= 2147483647) return null;
  const limits = [
    [input.timeoutSeconds, 1, Math.min(120, item.timeout_seconds)],
    [input.maxOutputBytes, 256, Math.min(65536, item.max_output_bytes)],
    [input.maxRuns, 1, Math.min(100, item.max_runs)],
  ];
  if (limits.some(([value, min, max]) => !Number.isInteger(value) || !Number.isInteger(max) || value < min || value > max)) return null;
  return {
    expected_policy_revision: revision,
    connection_instance_id: local.connection_instance_id!,
    session_id: local.session.session_id!,
    script_id: item.script_id,
    grant_digest: item.grant_digest,
    timeout_seconds: input.timeoutSeconds,
    max_output_bytes: input.maxOutputBytes,
    max_runs: input.maxRuns,
  };
}
