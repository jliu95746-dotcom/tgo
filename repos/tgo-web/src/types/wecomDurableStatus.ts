export const durableCountKeys = [
  'queued', 'processing', 'terminal_failure', 'completed', 'other', 'stale_lease',
] as const;

export type DurableCountKey = (typeof durableCountKeys)[number];

export type DurableCounts = Record<DurableCountKey, number>;

export const failureNoticeKeys = [
  'pending', 'sending', 'submitted', 'unknown', 'needs_attention',
] as const;

export type FailureNoticeKey = (typeof failureNoticeKeys)[number];

export type FailureNoticeCounts = Record<FailureNoticeKey, number>;

export interface WeComDurableStatus {
  checked_at: string;
  inbox: DurableCounts;
  media_jobs: DurableCounts;
  failure_notices: FailureNoticeCounts;
}
