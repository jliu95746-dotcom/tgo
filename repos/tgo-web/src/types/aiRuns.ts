export interface AIExecutionAttemptLookup {
  channel_id: string;
  source_message_id: string;
}

export interface AIExecutionAttemptSummary {
  execution_id: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  completed_at: string | null;
}

export interface AIExecutionToolEventSummary {
  sequence_no: number;
  tool_name: string;
  tool_call_id: string | null;
  status: 'started' | 'completed' | 'failed';
  occurred_at: string;
}

export interface AIExecutionBusinessQuerySummary {
  query_id: string;
  operation: 'order_query' | 'logistics_query' | 'customer_profile_query' | 'membership_query';
  outcome: 'passed' | 'failed';
  created_at: string;
}

export interface AIReplyOriginRequest {
  channel_id: string;
  client_msg_nos: string[];
}

export interface AIReplyOriginEvidence {
  client_msg_no: string;
  status: 'verified' | 'unrecorded' | 'missing' | 'mismatch' | 'unavailable';
}
