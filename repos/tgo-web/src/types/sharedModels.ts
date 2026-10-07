export type ModelPurpose = 'chat' | 'embedding' | 'asr' | 'ocr' | 'vlm';

export interface SharedModel {
  model_id: string;
  model_type: ModelPurpose;
  capabilities: Record<string, boolean> | null;
}

export interface SharedSelection {
  provider_id: string;
  model_id: string;
}

export interface SharedProvider {
  id: string;
  provider: string;
  name: string;
  api_base_url: string | null;
  config: Record<string, unknown> | null;
  default_model: string | null;
  is_active: boolean;
  models: SharedModel[];
  has_api_key: boolean;
}

export interface SharedModels {
  version: number;
  enabled: boolean;
  synchronization: 'inactive' | 'pending' | 'failed' | 'synced';
  providers: SharedProvider[];
  defaults: Partial<Record<ModelPurpose, SharedSelection>>;
}

export interface SharedProviderChange extends Omit<SharedProvider, 'has_api_key'> {
  api_key?: string;
}

export interface SharedModelsChange {
  expected_version: number;
  providers: SharedProviderChange[];
  defaults: Record<ModelPurpose, SharedSelection>;
  reason: string;
}

export interface SharedConnectionResult {
  provider_id: string;
  version: number;
  success: boolean;
  http_status: number | null;
  message: string;
  checked_at: string;
}
