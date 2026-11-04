/** Device-local reviewed metadata; never contains source, paths or commands. */
export interface ScriptParameter {
  name: string;
  kind: 'string' | 'integer' | 'boolean';
  required: boolean;
  minimum: number | null;
  maximum: number | null;
  max_length: number;
  choices: string[];
}

export interface ScriptDescriptor {
  script_id: string;
  title: string;
  version: string;
  grant_digest: string;
  effect: 'read_only' | 'write';
  parameters: ScriptParameter[];
  result_fields: ScriptParameter[];
  timeout_seconds: number;
  max_output_bytes: number;
  max_runs: number;
}

export interface ScriptCatalog {
  protocol_version: 1;
  scripts: ScriptDescriptor[];
}

export interface ScriptSessionStatus {
  protocol_version: 1;
  state: 'disabled' | 'unavailable' | 'awaiting_policy' | 'active' | 'closed';
  session_id: string | null;
  reason: string;
}

export interface ScriptLocalStatusRead {
  device_id: string;
  project_id: string;
  connection_instance_id: string | null;
  session: ScriptSessionStatus;
}

export interface ScriptPolicy {
  protocol_version: 1;
  revision: number;
  script_id: string;
  grant_digest: string;
  timeout_seconds: number;
  max_output_bytes: number;
  max_runs: number;
  digest: string;
}

export interface ScriptPolicyRead {
  device_id: string;
  project_id: string;
  policy: ScriptPolicy;
  status: 'pending' | 'active' | 'revoked';
  verified: false;
  updated_at: string;
  revoked_at: string | null;
}

export interface ScriptPolicyPublish {
  expected_policy_revision: number;
  connection_instance_id: string;
  session_id: string;
  script_id: string;
  grant_digest: string;
  timeout_seconds: number;
  max_output_bytes: number;
  max_runs: number;
}
