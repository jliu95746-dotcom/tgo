/** A live check of the employee's fixed device action, not an execution grant. */
export interface AgentDeviceActionReadiness {
  agent_id: string;
  device_id: string | null;
  tool_name: 'fs_read';
  status: 'not_bound' | 'ready' | 'needs_rebind' | 'needs_test'
    | 'legacy_unverified' | 'device_missing' | 'invalid_binding';
  policy_digest: string | null;
}
