export interface PlanDefinition {
  name: string; rank: number; monthly_price: number; annual_price: number;
  seats: number; monthly_ai: number; knowledge_bytes: number; channel_limit: number;
  seat_monthly_price: number; seat_annual_price: number; ai_pack_price: number; ai_pack_replies: number;
}
export interface BillingPlan { id: string; code: string; version: number; status: string; definition: PlanDefinition }
export type BillingKind = 'subscribe' | 'renew' | 'upgrade' | 'seats' | 'ai_pack';
export interface QuoteRequest { kind: BillingKind; plan_id?: string; months: 1 | 12; quantity?: number }
export interface BillingQuote {
  id: string; amount: number; currency: 'CNY'; expires_at: string;
  details: {
    kind: BillingKind; definition: PlanDefinition; months: 1 | 12; quantity: number;
    starts_at: string; ends_at: string; resulting_seats: number; additional_ai: number;
    extra_seats: number; subscription_expires_at: string | null;
  };
}
export interface BillingOrder {
  id: string; number: string; amount: number; payment_status: string; fulfillment_status: string;
  paid_at: string | null; created_at: string; expires_at: string; code_url: string | null;
  refunded_amount: number;
  project_id: string; failure_code: string | null; transaction_id: string | null;
}
export interface BillingSubscription {
  status: string; expires_at: string | null; plan: BillingPlan | null;
  seats: number | null; seats_used: number; seats_reserved: number; ai_remaining: number;
  server_time: string; months: 1 | 12 | null; version: number | null;
}
