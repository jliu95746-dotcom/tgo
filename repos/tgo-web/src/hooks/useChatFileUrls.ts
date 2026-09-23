import { useEffect, useState } from 'react';
import { useAuthStore } from '../stores/authStore';
import { authorizeChatFileUrl, chatFileId, chatFileLinksInText } from '../services/chatFileAccess';

export function useChatFileUrls(urls: readonly string[]): string[] {
  const identity = useAuthStore(state => state.user?.id ?? '');
  const serialized = JSON.stringify(urls);
  const key = `${identity}:${serialized}`;
  const [resolved, setResolved] = useState<{ key: string; urls: string[] } | null>(null);
  useEffect(() => {
    let active = true;
    const originals: string[] = JSON.parse(serialized);
    if (!originals.some(url => chatFileId(url))) return;
    const refresh = async () => {
      const values = await Promise.all(originals.map(url => authorizeChatFileUrl(url).catch(() => '')));
      if (active) setResolved({ key, urls: values });
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 60_000);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [key, serialized]);
  return resolved?.key === key ? resolved.urls : urls.map(url => chatFileId(url) ? '' : url);
}

export function useAuthorizedChatMarkdown(content: string): string {
  const originals = chatFileLinksInText(content);
  const urls = useChatFileUrls(originals);
  return originals.reduce((text, url, index) => text.split(url).join(urls[index] || ''), content);
}
