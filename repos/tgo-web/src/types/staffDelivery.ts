export type DeliveryJson = string | number | boolean | null | DeliveryJson[] | { [key: string]: DeliveryJson };

// Every accepted message has an integer type; additional payload fields depend on it.
export interface StaffDeliveryPayload { type: number; [key: string]: DeliveryJson }

export interface AssistTrainingIntent {
  skill_name: string;
  customer_message: string;
  ai_draft: string;
  source_message_id?: string;
  recent_messages: { role: 'customer' | 'staff' | 'assistant'; content: string }[];
}

export interface StaffDeliveryRequest {
  channel_id: string;
  channel_type: 251;
  client_msg_no: string;
  payload: StaffDeliveryPayload;
  retry_failed?: boolean;
  training?: AssistTrainingIntent | null;
}

export interface StaffDeliveryReceipt {
  client_msg_no: string;
  delivery_status: 'processing' | 'pending' | 'sent' | 'failed' | 'unknown';
  history_status: 'pending' | 'sent';
  error_code?: string | null;
  error_message?: string | null;
  training_status?: 'none' | 'pending' | 'saved' | 'unavailable';
  training_skill_name?: string | null;
}

export type StaffDeliveryQuery = Pick<StaffDeliveryRequest, 'channel_id' | 'channel_type' | 'client_msg_no'>;

export interface StaffDeliveryRecord {
  request: StaffDeliveryRequest;
  receipt: StaffDeliveryReceipt;
  created_at: string;
}
