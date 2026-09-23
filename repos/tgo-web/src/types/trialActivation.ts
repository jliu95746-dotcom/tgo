export interface TrialCodeRecord {
  id: string;
  created_at: string;
  expires_at: string;
  redeemed_at: string | null;
  redeemed_project_id: string | null;
}

export interface TrialCodeIssue extends TrialCodeRecord {
  code: string;
}
