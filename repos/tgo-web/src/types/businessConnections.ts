/** Stored connection responses contain no credentials; only writes accept them. */
export type BusinessCapability = 'order_query' | 'logistics_query' | 'customer_profile_query' | 'membership_query';
export interface BusinessHTTPConfiguration {
  base_url: string;
  order_path: string;
  logistics_path: string;
  customer_profile_path: string | null;
  membership_path: string | null;
  method: 'GET' | 'POST';
  timeout_seconds: number;
  auth_mode: 'none' | 'bearer' | 'api_key' | 'basic';
  api_key_header: string;
  data_path: string;
  success_field: string | null;
  success_value: string;
  tenant_id_field: string;
  customer_id_field: string;
  order_no_field: string;
  order_status_field: string;
  order_amount_field: string;
  order_amount_unit: 'minor' | 'major';
  order_currency_field: string;
  order_created_at_field: string;
  logistics_status_field: string;
  logistics_carrier_field: string;
  logistics_tracking_no_field: string;
  logistics_updated_at_field: string;
  customer_segment_field: string;
  customer_tags_field: string;
  membership_tier_field: string;
  membership_points_field: string;
  merchant_id: string | null;
  request_order_no_field: string;
  request_customer_id_field: string;
  request_merchant_id_field: string;
  request_operation_field: string | null;
  order_operation_value: string;
  logistics_operation_value: string;
  order_status_map: Record<string, string>;
  logistics_status_map: Record<string, string>;
  not_found_values: string[];
  logistics_not_shipped_values: string[];
  default_timezone: string;
}
export interface BusinessConnectionCredentialsInput {
  token?: string;
  username?: string;
  password?: string;
}
export interface BusinessConnection {
  id: string;
  project_id: string;
  name: string;
  mode: 'http' | 'demo';
  capabilities: BusinessCapability[];
  configuration: BusinessHTTPConfiguration | null;
  credentials_configured: boolean;
  enabled: boolean;
  revision: number;
  configuration_revision: number;
  authorization_revision: number;
  resource_revision: string;
  test_status: 'not_tested' | 'passed' | 'unavailable';
  created_at: string;
  updated_at: string;
}
export interface BusinessConnectionCreate {
  request_id: string;
  name: string;
  mode: 'http' | 'demo';
  capabilities?: BusinessCapability[];
  configuration?: Partial<BusinessHTTPConfiguration> & Pick<BusinessHTTPConfiguration, 'base_url'>;
  credentials?: BusinessConnectionCredentialsInput;
  enabled?: false;
}
export interface BusinessConnectionUpdate {
  expected_revision: number;
  name?: string;
  mode?: 'http' | 'demo';
  capabilities?: BusinessCapability[];
  configuration?: (Partial<BusinessHTTPConfiguration> & Pick<BusinessHTTPConfiguration, 'base_url'>) | null;
  credentials?: BusinessConnectionCredentialsInput | null;
  enabled?: boolean;
}
export interface BusinessConnectionTestRequest {
  request_id: string;
  expected_revision: number;
  operation: BusinessCapability;
  order_no?: string | null;
  customer_id: string;
}

export interface BusinessConnectionTestReport {
  id: string;
  request_id: string;
  connection_id: string;
  operation: BusinessCapability;
  mode: 'http' | 'demo';
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  outcome: 'passed' | 'failed' | 'demo' | null;
  configuration_revision: number;
  summary: BusinessConnectionTestSummary | null;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  expires_at: string;
  applicability: BusinessConnectionTestApplicability;
  scope: 'business_connection_test';
  can_enable: false;
}

export interface BusinessConnectionTestApplicability {
  scope: 'business_connection_configuration';
  status: 'current' | 'stale' | 'not_passed' | 'unavailable';
  checked_at: string;
  valid_until: string | null;
  issues: Array<'test_not_passed' | 'demo_only' | 'configuration_changed' | 'capability_removed' | 'superseded' | 'expired' | 'reviewer_unavailable' | 'credential_unavailable' | 'evidence_unknown'>;
}

export interface BusinessConnectionTestSummary {
  status: string;
  amount_minor: number | null;
  currency: string | null;
  carrier: string | null;
  tracking_no_masked: string | null;
  segment: string | null;
  service_tags: string[] | null;
  tier: string | null;
  points_balance: number | null;
}
