export const commercialHealthMetrics = [
  'paid_unfulfilled', 'fulfillment_conflicts', 'overdue_jobs',
  'expired_job_leases', 'review_jobs', 'stale_ai_reservations',
  'review_ai_reservations', 'statement_differences',
] as const;

export interface CommercialHealth {
  checked_at: string;
  status: 'clear' | 'attention';
  counts: Partial<Record<(typeof commercialHealthMetrics)[number], number>>;
  will_change_data: false;
}
