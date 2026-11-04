import { apiClient } from './api';

const avatarPath = /^\/(?:api\/)?v1\/visitors\/([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})\/avatar\/?$/i;

export function avatarSessionDeadline(token: string | null): number {
  if (!token) return 0;
  try {
    const data: unknown = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof data === 'object' && data !== null && 'exp' in data && typeof data.exp === 'number'
      ? data.exp * 1000 : 0;
  } catch { return 0; }
}

/** Treat received URLs as locators; send authority only to our configured API. */
export function privateVisitorAvatarPath(url: string): string | null {
  try {
    const parsed = new URL(url, window.location.origin);
    const match = parsed.pathname.match(avatarPath);
    if (!match) return null;
    const versions = parsed.searchParams.getAll('v');
    if (versions.length > 1) return '';
    const version = versions[0];
    if (version !== undefined && !/^[a-z\d_-]+\.(?:png|jpe?g|gif|webp)$/i.test(version)) return '';
    return `/v1/visitors/${match[1]}/avatar${version ? `?v=${encodeURIComponent(version)}` : ''}`;
  } catch { return ''; }
}

export async function readVisitorAvatar(url: string, token: string | null, signal?: AbortSignal): Promise<Blob> {
  const path = privateVisitorAvatarPath(url);
  if (!path || !token || apiClient.getToken() !== token) throw new Error('Avatar session changed');
  const blob = await apiClient.getPrivateImage(path, token, signal);
  if (apiClient.getToken() !== token) throw new Error('Avatar session changed');
  return blob;
}
