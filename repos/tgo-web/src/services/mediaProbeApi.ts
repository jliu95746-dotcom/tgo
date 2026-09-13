import { apiClient } from './api';
import type { MediaProbeResult, MediaProbeTarget } from '@/types/mediaProbe';

export async function probeMediaModel(target: MediaProbeTarget, file: File): Promise<MediaProbeResult> {
  const data = new FormData();
  data.append('provider_id', target.providerId);
  data.append('model_id', target.modelId);
  data.append('capability', target.capability);
  data.append('file', file);
  return apiClient.postFormData<MediaProbeResult>('/v1/ai/providers/probe-media', data);
}
