import type { OperationsCompany } from './billingSupport';

export interface OperatorAudit {
  id: string;
  project_id: string | null;
  operator_name: string;
  action: string;
  reason: string;
  created_at: string;
  detail: Record<string, unknown>;
}

export interface ManagedMember {
  id: string;
  username: string;
  name: string | null;
  role: string;
  account_enabled: boolean;
  email_verified: boolean;
  token_version: number;
  open_sessions: number;
}

export interface CompanyDirectory {
  data: OperationsCompany[];
  total: number;
  offset: number;
  limit: number;
}

export interface CompanyDetail {
  company: OperationsCompany;
  created_at: string;
  plan_id: string | null;
  version: number;
  operator_override_until: string | null;
  members: ManagedMember[];
  audits: OperatorAudit[];
}

export interface AuthorizationChange {
  request_id: string;
  expected_version: number;
  plan_id: string;
  expires_at: string;
  seats: number;
  ai_credits: number;
  reason: string;
}

export interface AuthorizationSnapshot {
  plan_id: string | null;
  plan_name: string | null;
  status: string;
  expires_at: string | null;
  seats: number | null;
  version: number;
  ai_credits: number;
}

export interface AuthorizationPreview {
  before: AuthorizationSnapshot;
  after: AuthorizationSnapshot;
  used_seats: number;
  reserved_seats: number;
}

export interface MemberControl {
  expected_token_version: number;
  role?: 'admin' | 'user';
  account_enabled?: boolean;
  reason: string;
}

export interface OperationsOverview {
  total_companies: number;
  enabled_companies: number;
  expiring_companies: number;
  unresolved_orders: number;
  failed_tasks: number;
  pending_invoices: number;
  checked_at: string;
}
