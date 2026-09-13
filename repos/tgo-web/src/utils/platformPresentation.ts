import type { Platform } from '@/types';

/** Activation is not proof that the external channel is reachable. */
export function platformDisplayStatus(platform: Pick<Platform, 'status' | 'is_configured'>) {
  if (platform.status === 'disabled') return 'disabled';
  if (platform.is_configured !== true) return 'unconfigured';
  if (platform.status === 'error') return 'error';
  return 'enabled';
}
