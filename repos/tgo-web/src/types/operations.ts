export interface OperatorProfile {
  id: string;
  email: string;
  name: string;
}

export interface OperatorLoginRequest {
  email: string;
  password: string;
}

export interface OperatorLoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_at: string;
  operator: OperatorProfile;
}

export interface OperationsStatus {
  enabled: boolean;
  login_available: boolean;
}

export interface CompanyMigrationPreview {
  project_id: string;
  name: string;
  created_at: string;
  human_accounts: number;
  administrator_accounts: number;
  action: 'review_required';
  requires_admin_recovery: boolean;
}

export interface MigrationPreviewResponse {
  will_change_data: false;
  data: CompanyMigrationPreview[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_next: boolean;
    has_prev: boolean;
  };
}
