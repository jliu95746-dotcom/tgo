export interface PlatformModelDefinition {
  model: string;
  provider_kind: 'openai' | 'openai_compatible' | 'anthropic' | 'google';
  api_base_url: string | null;
  vendor: string | null;
  active: boolean;
  input_fen_per_million: string | null;
  output_fen_per_million: string | null;
}
export interface PlatformModelPolicy {
  version: number;
  definition: PlatformModelDefinition | null;
  has_api_key: boolean;
}
export interface PlatformModelChange extends PlatformModelDefinition {
  expected_version: number;
  api_key?: string;
  reason: string;
}
