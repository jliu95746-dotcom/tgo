export interface InvoiceInput { order_id: string; title: string; tax_number: string; email: string }
export interface InvoiceRequest extends InvoiceInput {
  id: string; project_id: string; status: string; invoice_number: string | null; created_at: string;
}
export interface QuotaBatch {
  id: string; kind: string; amount: number; remaining: number; expires_at: string;
  created_at: string; order_id: string | null;
}
export interface QuotaReply {
  id: string; project_id: string; round_key: string; batch_id: string; status: string;
  attempt: number; created_at: string; settled_at: string | null;
}
export interface RefundInput {
  order_id: string; amount: number; reason: string; entitlement_action: 'keep' | 'suspend';
}
export interface Refund {
  id: string; number: string; project_id: string; order_id: string; amount: number;
  reason: string; status: string; provider_id: string | null; succeeded_at: string | null;
  created_at: string;
  disposition: {
    action: 'keep' | 'suspend'; original_order_amount: number; already_refunded: number;
    available_ai_from_order: number; granted_ai_from_order: number; company_status: string;
    subscription_version: number | null; order_kind: string; manual_review_required: boolean;
  };
}
export interface Reconciliation {
  bill_date: string; status: string; entry_count: number; issue_count: number; completed_at: string;
  issues: { code: string; order_number: string; project_id: string | null }[];
}
export interface OperationsCompany {
  id: string; name: string; status: string; plan_name: string | null; expires_at: string | null;
  seats: number | null; used: number; reserved: number; ai_remaining: number; order_exceptions: number;
}
export interface CreditAdjustment { request_id: string; delta: number; expires_at?: string; reason: string }
