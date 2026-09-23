export interface OperationsTask {
  id: string;
  project_id: string | null;
  order_id: string | null;
  kind: string;
  status: string;
  attempts: number;
  available_at: string;
  locked_until: string | null;
  last_error: string | null;
}

export interface OperationsAudit {
  id: string;
  project_id: string | null;
  operator_id: string;
  action: string;
  reason: string;
  created_at: string;
}

export interface CommercialReadiness {
  billing_enabled: boolean;
  purchases_enabled: boolean;
  registration_enabled: boolean;
  checks: { code: 'email' | 'wechat' | 'refund_callback' | 'internal_auth' | 'platform_model'; configured: boolean }[];
}
