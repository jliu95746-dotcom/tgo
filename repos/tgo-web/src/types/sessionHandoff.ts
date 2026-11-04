export interface SessionHandoffExcerpt {
  role: 'customer' | 'staff' | 'assistant';
  content: string;
  message_seq: number;
  timestamp: number;
}

export interface SessionHandoffSummary {
  session_id: string;
  visitor_id: string;
  sampled_message_count: number;
  has_earlier_messages: boolean;
  latest_customer_messages: SessionHandoffExcerpt[];
  latest_replies: SessionHandoffExcerpt[];
}
