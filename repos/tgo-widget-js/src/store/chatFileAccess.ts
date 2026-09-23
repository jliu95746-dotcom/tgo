import { usePlatformStore } from './platformStore'
import { authorizeChatFileUrl, authorizeChatMarkdown } from '../services/chatFileAccess'

export function useChatFileUrls(urls: readonly string[]): string[] {
  const base = usePlatformStore(state => state._apiBase)
  const key = usePlatformStore(state => state._platformApiKey)
  return urls.map(url => authorizeChatFileUrl(url, base, key))
}

export function useAuthorizedChatMarkdown(content: string): string {
  const base = usePlatformStore(state => state._apiBase)
  const key = usePlatformStore(state => state._platformApiKey)
  return authorizeChatMarkdown(content, base, key)
}
