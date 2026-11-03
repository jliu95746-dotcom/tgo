import { useCallback, useEffect, useRef } from 'react';
import type { ChannelInfo } from '@/types';
import { channelApiService } from '@/services/channelApi';
import { createReceptionModeSync } from '@/services/receptionModeSync';
import { wukongimWebSocketService } from '@/services/wukongimWebSocket';
import { useAuthStore } from '@/stores/authStore';
import { useChannelStore } from '@/stores/channelStore';
import { useChatStore } from '@/stores/chatStore';
import { channelResponseToChannelInfo } from '@/utils/channelTransforms';
import { getChannelKey } from '@/utils/channelUtils';

/** Recover missed events in independent mobile/desktop webviews without changing modes. */
export function useReceptionModeSync(channelId?: string, channelType?: number, visitorId?: string) {
  const actor = useAuthStore(state => state.user?.id);
  const current = useRef<{
    key: string;
    sync: ReturnType<typeof createReceptionModeSync<ChannelInfo>>;
  } | null>(null);
  const key = `${actor}:${channelId}:${channelType}:${visitorId}`;
  useEffect(() => {
    if (!actor || !visitorId || !channelId || channelType !== 251) return;
    let warned = false;
    const sync = createReceptionModeSync<ChannelInfo>({
      read: async () => {
        // Older cache requests must finish before the authoritative follow-up read.
        const inflight = useChannelStore.getState().inFlight[getChannelKey(channelId, channelType)];
        if (inflight) await inflight.catch(() => null);
        return channelResponseToChannelInfo(await channelApiService.getChannelInfo({ channel_id: channelId, channel_type: channelType }));
      },
      apply: info => {
        warned = false;
        const store = useChannelStore.getState();
        const cached = store.getChannel(channelId, channelType);
        if (JSON.stringify(cached) === JSON.stringify(info)) return;
        store.seedChannel(channelId, channelType, info);
        useChatStore.getState().applyChannelInfo(channelId, channelType, info);
      },
    });
    current.current = { key, sync };
    const refresh = () => {
      if (document.hidden) return;
      sync.invalidate();
      void sync.refresh().catch(error => {
        if (!warned) console.warn('Reception state synchronization failed:', error instanceof Error ? error.message : 'unknown error');
        warned = true;
      });
    };
    const unsubscribeProfile = wukongimWebSocketService.onVisitorProfileUpdated(event => {
      if (event.channelId === channelId && event.channelType === channelType) refresh();
    });
    const unsubscribeConnection = wukongimWebSocketService.onConnectionStatus(status => { if (status.isConnected) refresh(); });
    const interval = window.setInterval(() => {
      if (!document.hidden && navigator.onLine !== false) {
        void sync.refresh().catch(error => {
          if (!warned) console.warn('Reception state synchronization failed:', error instanceof Error ? error.message : 'unknown error');
          warned = true;
        });
      }
    }, 3000);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    window.addEventListener('pageshow', refresh);
    window.addEventListener('online', refresh);
    refresh();
    return () => {
      sync.dispose();
      if (current.current?.sync === sync) current.current = null;
      unsubscribeProfile(); unsubscribeConnection(); window.clearInterval(interval);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('pageshow', refresh);
      window.removeEventListener('online', refresh);
    };
  }, [actor, channelId, channelType, visitorId, key]);

  const refreshForAction = useCallback(async () => {
    const target = current.current;
    if (!target || target.key !== key) return null;
    target.sync.invalidate();
    return target.sync.refresh();
  }, [key]);
  const beginMutation = useCallback(() => {
    if (current.current?.key === key) current.current.sync.pause();
  }, [key]);
  const endMutation = useCallback(async () => {
    if (current.current?.key === key) await current.current.sync.resume();
  }, [key]);
  return { refreshForAction, beginMutation, endMutation };
}
