export type ShipmentStatus =
  | 'unknown'
  | 'pending'
  | 'active'
  | 'in_transit'
  | 'delivered'
  | 'exception';

export interface LogisticsSettings {
  id: string | null;
  project_id: string;
  enabled: boolean;
  auto_capture_visitor_messages: boolean;
  auto_capture_staff_messages: boolean;
  verify_before_binding: boolean;
  auto_query_on_mention: boolean;
  query_tool_id: string | null;
  poll_interval_minutes: number;
  stop_after_delivered: boolean;
  archive_after_days: number;
  conflict_policy: 'manual_review' | 'keep_first';
  created_at: string | null;
  updated_at: string | null;
}

export type LogisticsSettingsUpdate = Omit<
  LogisticsSettings,
  'id' | 'project_id' | 'created_at' | 'updated_at'
>;

export interface CustomerShipment {
  id: string;
  visitor_id: string;
  tracking_no_masked: string;
  carrier_code: string | null;
  carrier_name: string | null;
  status: ShipmentStatus;
  source: 'visitor_message' | 'staff_message' | 'manual' | 'order_sync';
  verification_state: 'pending' | 'verified' | 'conflict';
  latest_summary: string | null;
  last_checked_at: string | null;
  delivered_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShipmentTrackingEvent {
  id: string;
  shipment_id: string;
  status: string | null;
  description: string;
  location: string | null;
  event_time: string;
}

export interface ShipmentQueryResult {
  shipment: CustomerShipment;
  events: ShipmentTrackingEvent[];
  queried_live: boolean;
  message: string;
}
