import { usePlatformStore } from './platformStore'
import { useChatStore } from './chatStore'
import { useEffect, useState } from 'react'
import { readChatFileAccess, authorizeChatMarkdown, maskPrivateChatMarkdown } from '../services/chatFileAccess'

export function useChatFileUrls(urls: readonly string[]): string[] {
  const base = usePlatformStore(state => state._apiBase)
  const key = usePlatformStore(state => state._platformApiKey)
  const revision = useChatStore(state => state.sessionRevision)
  const scope = JSON.stringify([base,key,revision,urls])
  const [result,setResult] = useState<{scope:string;urls:string[]}>({scope:'',urls:[]})
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const sources: string[] = JSON.parse(scope)[3]
    const refresh = async () => {
      const links = await Promise.all(sources.map(url => readChatFileAccess(url,base,key).catch(() => ({url:''}))))
      if (!active) return
      setResult({scope,urls:links.map(link => link.url)})
      const expiry = Math.min(...links.map(link => 'expiresAt' in link && typeof link.expiresAt === 'number' ? link.expiresAt : Infinity))
      if (Number.isFinite(expiry)) timer = setTimeout(() => {void refresh()}, Math.max(1000,Math.min(240000,expiry-Date.now()-5000)))
    }
    void refresh()
    return () => {active=false;if(timer)clearTimeout(timer)}
  }, [scope,base,key])
  return result.scope === scope ? result.urls : urls.map(() => '')
}

export function useAuthorizedChatMarkdown(content: string): string {
  const base = usePlatformStore(state => state._apiBase)
  const key = usePlatformStore(state => state._platformApiKey)
  const revision = useChatStore(state => state.sessionRevision)
  const scope = JSON.stringify([base,key,revision,content])
  const [result,setResult] = useState({scope:'',content:''})
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const refresh = async () => {
      const value = await authorizeChatMarkdown(content,base,key)
      if (active) {
        setResult({scope,content:value})
        if (maskPrivateChatMarkdown(content) !== content) timer=setTimeout(() => {void refresh()},60000)
      }
    }
    void refresh()
    return () => {active=false;if(timer)clearTimeout(timer)}
  }, [scope,base,key,content])
  return result.scope === scope ? result.content : maskPrivateChatMarkdown(content)
}
