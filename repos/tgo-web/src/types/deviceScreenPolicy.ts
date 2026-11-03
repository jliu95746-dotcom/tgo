export interface ScreenRegion { x: number; y: number; width: number; height: number }
export interface ScreenGrant {
  protocol_version: 1; active: boolean; grant_id: string | null;
  width: number; height: number; dpi: number;
  allow_click: boolean; allow_text: boolean; click_regions: ScreenRegion[];
  max_actions: number; used_actions: number;
}
export interface ScreenLocalGrant {
  device_id: string; project_id: string;
  connection_instance_id: string | null; grant: ScreenGrant;
}
export interface ScreenPolicy {
  protocol_version: 1; revision: number; grant_id: string;
  max_actions: number; allow_click: boolean; allow_text: boolean;
  click_regions: ScreenRegion[]; digest: string;
}
export interface DeviceScreenPolicy {
  device_id: string; project_id: string; policy: ScreenPolicy;
  status: 'pending' | 'active' | 'revoked'; verified: false;
  updated_at: string; revoked_at: string | null;
}
export interface ScreenPolicyPublish {
  expected_policy_revision: number; connection_instance_id: string;
  grant_id: string; max_actions: number; allow_click: boolean;
  allow_text: boolean; click_regions: ScreenRegion[];
}
