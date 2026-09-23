import { makeChatFileUrl } from './upload'

export function authorizeChatFileUrl(url: string, apiBase?: string, apiKey?: string): string {
  let id: string | undefined
  try {
    id = new URL(url, window.location.origin).pathname.match(/\/v1\/chat\/files\/([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})\/?$/i)?.[1]
  } catch { return url }
  if (!id) return url
  if (!apiBase || !apiKey) return ''
  // Always build against the configured API. Never send the key to a message URL.
  return makeChatFileUrl(apiBase, id, { apiKeyOverride: apiKey })
}

export function authorizeChatMarkdown(content: string, apiBase?: string, apiKey?: string): string {
  return content.replace(/(?:https?:\/\/[^\s<>()"']+)?(?:\/api)?\/v1\/chat\/files\/[a-f\d-]{36}(?:\?[^\s<>()"']*)?/gi,
    url => authorizeChatFileUrl(url, apiBase, apiKey))
}
