/** Admin metadata has no merchant signing key or customer session token. */
export interface CustomerIdentityPolicy {
  scope: 'business_customer_identity_configuration'; can_enable: false;
  id: string; project_id: string; connection_id: string; platform_ids: string[];
  enabled: boolean; revision: number; connection_configuration_revision: number;
  max_session_seconds: number; signing_key_configured: boolean;
}
export interface CustomerIdentityPolicyWrite {
  expected_revision: number; platform_ids: string[]; signing_secret?: string;
  enabled: boolean; max_session_seconds: number;
}
export interface CustomerIdentityBinding {
  id: string; visitor_id: string; platform_id: string; revision: number;
  expires_at: string; revoked_at: string | null;
  status: 'current' | 'expired' | 'revoked' | 'stale'; can_enable: false;
}
