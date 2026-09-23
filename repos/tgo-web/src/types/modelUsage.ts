export interface ModelUsage {
  id: string;
  project_id: string;
  reservation_id: string | null;
  model_name: string;
  purpose: string;
  status: 'running' | 'succeeded' | 'failed' | 'cancelled';
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_fen: string | null;
  currency: 'CNY';
  created_at: string;
  completed_at: string | null;
}
