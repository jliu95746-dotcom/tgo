import { apiClient } from './api';
import type { ChatFileAccessResponse } from '../types/chatFileAccess';

const filePath = /\/v1\/chat\/files\/([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})\/?$/i;
export function chatFileId(url: string): string | null {
  try { return new URL(url, window.location.origin).pathname.match(filePath)?.[1] ?? null; }
  catch { return null; }
}

let session: string | null = null;
const links = new Map<string, ChatFileAccessResponse>();
const pending = new Map<string, Promise<string>>();

export async function authorizeChatFileUrl(url: string): Promise<string> {
  const id = chatFileId(url);
  if (!id) return url;
  const token = apiClient.getToken();
  if (session !== token) { session = token; links.clear(); pending.clear(); }
  const cached = links.get(id);
  if (cached && Date.parse(cached.expires_at) > Date.now() + 90_000) return cached.access_url;
  const underway = pending.get(id);
  if (underway) return underway;
  const request = apiClient.post<ChatFileAccessResponse>(`/v1/chat/files/${id}/access`).then(result => {
    if (session !== token) throw new Error('Attachment session changed');
    links.set(id, result);
    return result.access_url;
  }).finally(() => { if (session === token) pending.delete(id); });
  pending.set(id, request);
  return request;
}

export const chatFileLinksInText = (text: string): string[] =>
  [...new Set(text.match(/(?:https?:\/\/[^\s<>()"']+)?(?:\/api)?\/v1\/chat\/files\/[a-f\d-]{36}(?:\?[^\s<>()"']*)?/gi) ?? [])];
