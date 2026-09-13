export type MediaCapability = 'asr' | 'ocr' | 'vlm';
export interface MediaProbeTarget {
  providerId: string;
  providerName: string;
  modelId: string;
  capability: MediaCapability;
}
export interface MediaProbeResult {
  success: boolean;
  message: string;
  error_code?: string | null;
  output?: string | null;
}
