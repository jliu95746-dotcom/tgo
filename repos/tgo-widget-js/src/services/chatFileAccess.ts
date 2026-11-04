import { captureVisitorDataAccess } from './visitorDataAccess'

export interface ChatFileUrlAccess { url: string; expiresAt?: number }

export async function readChatFileAccess(url: string, apiBase?: string, apiKey?: string): Promise<ChatFileUrlAccess> {
  let id: string | undefined
  try {
    id = new URL(url, window.location.origin).pathname.match(/\/v1\/chat\/files\/([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})\/?$/i)?.[1]
  } catch { return {url} }
  if (!id) return {url}
  if (!apiBase || !apiKey) return {url:''}
  const access = captureVisitorDataAccess({apiBase, platformApiKey:apiKey})
  const endpoint = new URL(`${apiBase.replace(/\/+$/, '')}/v1/chat/files/${id}`)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 10000)
  try {
    const response = await fetch(`${endpoint}/access`, {method:'POST', signal:controller.signal,
      headers:{...access.headers,'X-Platform-API-Key':apiKey}})
    access.assertCurrent()
    if (!response.ok) throw new Error('附件访问失败，请重新连接后重试。')
    const data: unknown = await response.json()
    access.assertCurrent()
    access.assertSafeValue(data)
    if (!data || typeof data !== 'object' || !('access_url' in data) || typeof data.access_url !== 'string'
        || !('expires_at' in data) || typeof data.expires_at !== 'string') throw new Error('附件响应无效。')
    const link = new URL(data.access_url, endpoint)
    const fileToken = link.searchParams.get('file_token')
    const expiresAt = Date.parse(data.expires_at)
    if (link.origin !== endpoint.origin || link.pathname !== endpoint.pathname || link.username || link.password
        || !fileToken || fileToken.length > 16384 || [...link.searchParams.keys()].some(key => key !== 'file_token')
        || link.searchParams.getAll('file_token').length !== 1 || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
      throw new Error('附件链接未通过安全检查。')
    }
    return {url:link.href, expiresAt}
  } finally {clearTimeout(timer)}
}

export async function authorizeChatFileUrl(url: string, apiBase?: string, apiKey?: string): Promise<string> {
  return (await readChatFileAccess(url, apiBase, apiKey)).url
}

const privateFiles = /(?:https?:\/\/[^\s<>()"']+)?(?:\/api)?\/v1\/chat\/files\/[a-f\d-]{36}(?:\?[^\s<>()"']*)?/gi

export function maskPrivateChatMarkdown(content: string): string {
  return content.replace(privateFiles, '')
}

export async function authorizeChatMarkdown(content: string, apiBase?: string, apiKey?: string): Promise<string> {
  const urls = [...new Set(content.match(privateFiles) || [])]
  const authorized = await Promise.all(urls.map(url => authorizeChatFileUrl(url, apiBase, apiKey).catch(() => '')))
  const links = new Map(urls.map((url,index) => [url, authorized[index]]))
  return content.replace(privateFiles, url => links.get(url) || '')
}
