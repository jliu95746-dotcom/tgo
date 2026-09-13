import type { MediaCapability } from '@/types/mediaProbe';
export const mediaProbeAccept = {
  asr: 'audio/wav,audio/x-wav,audio/mpeg,audio/mp4,audio/x-m4a,audio/flac,audio/ogg,audio/webm',
  ocr: 'image/png,image/jpeg,image/webp',
  vlm: 'image/png,image/jpeg,image/webp',
};
export function mediaProbeFileError(file: Pick<File, 'size' | 'type'>, capability: MediaCapability): string | null {
  if (!file.size || file.size > 5 * 1024 * 1024) return 'size';
  if (!mediaProbeAccept[capability].split(',').includes(file.type)) return 'type';
  return null;
}
