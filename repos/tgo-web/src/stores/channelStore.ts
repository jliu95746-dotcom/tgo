import { create } from 'zustand';
import { channelApiService, type ChannelInfoApiResponse } from '@/services/channelApi';
import type { ChannelInfo } from '@/types';
import { channelResponseToChannelInfo, fallbackChannelName } from '@/utils/channelTransforms';
import { getChannelKey } from '@/utils/channelUtils';
export { getChannelKey } from '@/utils/channelUtils';
import { DEFAULT_CHANNEL_TYPE } from '@/constants';


interface ChannelStoreState {
  channels: Record<string, ChannelInfo>;
  inFlight: Partial<Record<string, Promise<ChannelInfo | null>>>;
  errors: Partial<Record<string, string | null>>;
  getChannel: (channel_id: string, channel_type: number) => ChannelInfo | undefined;
  seedChannel: (channel_id: string, channel_type: number, data: Partial<ChannelInfo>) => void;
  ensureChannel: (params: { channel_id: string; channel_type: number; force?: boolean }) => Promise<ChannelInfo | null>;
  refreshChannel: (params: { channel_id: string; channel_type: number }) => Promise<ChannelInfo | null>;
  /** Update presence fields on cached channel info (no fetch). */
  updateVisitorOnlineStatus: (channel_id: string, channel_type: number, is_online: boolean, last_offline_iso?: string | null) => void;
  /** Update avatar on cached channel info (no fetch). */
  updateChannelAvatar: (channel_id: string, channel_type: number, avatar: string) => void;
  /** Update extra fields on cached channel info (no fetch). */
  updateChannelExtra: (channel_id: string, channel_type: number, extraUpdates: Record<string, any>) => void;
  clear: () => void;
}

const omitKey = <T extends Record<string, unknown>>(record: T, key: string): T => {
  if (!(key in record)) return record;
  const { [key]: _omit, ...rest } = record;
  return rest as T;
};

export const useChannelStore = create<ChannelStoreState>((set, get) => ({
  channels: {},
  inFlight: {},
  errors: {},

  getChannel: (channelId: string, channelType: number) => {
    if (!channelId || !Number.isFinite(channelType)) return undefined;
    const key = getChannelKey(channelId, channelType);
    return get().channels[key];
  },

  seedChannel: (channelId: string, channelType: number, data: Partial<ChannelInfo>) => {
    if (!channelId || !Number.isFinite(channelType)) return;
    const key = getChannelKey(channelId, channelType);
    set((state) => ({
      channels: {
        ...state.channels,
        [key]: {
          name: data.name ?? state.channels[key]?.name ?? fallbackChannelName(channelId),
          avatar: data.avatar ?? state.channels[key]?.avatar ?? '',
          channel_id: channelId,
          channel_type: (data.channel_type ?? state.channels[key]?.channel_type ?? channelType) as number,
          extra: data.extra ?? state.channels[key]?.extra ?? null,
        },
      },
    }));
  },

  ensureChannel: async (params: { channel_id: string; channel_type: number; force?: boolean }) => {
    const channelId = params?.channel_id;
    const channelType = params?.channel_type ?? DEFAULT_CHANNEL_TYPE;
    const force = params?.force === true;

    if (!channelId) return null;

    const key = getChannelKey(channelId, channelType);

    const { channels, inFlight, errors } = get();
    const cached = channels[key];

    if (cached && !force) {
      return cached;
    }

    if (inFlight[key]) {
      return inFlight[key] as Promise<ChannelInfo | null>;
    }

    if (errors[key] && !force) {
      return cached ?? null;
    }

    const fetchPromise = channelApiService
      .getChannelInfo({ channel_id: channelId, channel_type: channelType })
      .then((response: ChannelInfoApiResponse) => {
        const info = channelResponseToChannelInfo(response);
        set((state) => ({
          channels: { ...state.channels, [key]: info },
          inFlight: omitKey(state.inFlight, key),
          errors: { ...state.errors, [key]: null },
        }));
        return info;
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : '获取频道信息失败';
        set((state) => ({
          channels: {
            ...state.channels,
            [key]: state.channels[key] ?? {
              name: fallbackChannelName(channelId),
              avatar: '',
              channel_id: channelId,
              channel_type: channelType,
              extra: null,
            },
          },
          inFlight: omitKey(state.inFlight, key),
          errors: { ...state.errors, [key]: message },
        }));
        throw error;
      });

    set((state) => ({
      inFlight: { ...state.inFlight, [key]: fetchPromise },
      errors: { ...state.errors, [key]: null },
    }));

    return fetchPromise;
  },

  refreshChannel: async (params: { channel_id: string; channel_type: number }) => {
    return get().ensureChannel({ ...params, force: true });
  },

  updateVisitorOnlineStatus: (channel_id: string, channel_type: number, is_online: boolean, last_offline_iso?: string | null) => {
    if (!channel_id || !Number.isFinite(channel_type)) return;
    const key = getChannelKey(channel_id, channel_type);
    set((state) => {
      const prev = state.channels[key];
      if (!prev) return {} as any;
      const prevExtra: any = prev.extra ?? {};
      const prevOnline = typeof prevExtra?.is_online === 'boolean' ? prevExtra.is_online : undefined;
      const prevLast = typeof prevExtra?.last_offline_time === 'string' ? prevExtra.last_offline_time : undefined;
      const nextLast = is_online ? (prevLast ?? null) : (last_offline_iso ?? prevLast ?? null);

      // Only update if something actually changes
      if (prevOnline === is_online && (prevLast ?? null) === (nextLast ?? null)) {
        return {} as any;
      }

      const nextExtra = { ...prevExtra, is_online, last_offline_time: nextLast };
      const nextInfo: ChannelInfo = { ...prev, extra: nextExtra };
      return {
        channels: { ...state.channels, [key]: nextInfo }
      } as any;
    });
  },

  updateChannelAvatar: (channel_id: string, channel_type: number, avatar: string) => {
    if (!channel_id || !Number.isFinite(channel_type)) return;
    const key = getChannelKey(channel_id, channel_type);
    set((state) => {
      const prev = state.channels[key];
      if (!prev) return {} as any;
      
      // Only update if avatar actually changes
      if (prev.avatar === avatar) {
        return {} as any;
      }

      const nextInfo: ChannelInfo = { ...prev, avatar };
      // Also update avatar_url in extra if it exists
      if (nextInfo.extra && typeof nextInfo.extra === 'object') {
        nextInfo.extra = { ...nextInfo.extra, avatar_url: avatar };
      }
      return {
        channels: { ...state.channels, [key]: nextInfo }
      } as any;
    });
  },

  updateChannelExtra: (channel_id: string, channel_type: number, extraUpdates: Record<string, any>) => {
    if (!channel_id || !Number.isFinite(channel_type)) return;
    const key = getChannelKey(channel_id, channel_type);
    set((state) => {
      const prev = state.channels[key];
      if (!prev) return {} as any;
      
      const prevExtra: any = prev.extra ?? {};
      const nextExtra = { ...prevExtra, ...extraUpdates };
      const nextInfo: ChannelInfo = { ...prev, extra: nextExtra };
      return {
        channels: { ...state.channels, [key]: nextInfo }
      } as any;
    });
  },

  clear: () => set({ channels: {}, inFlight: {}, errors: {} }),
}));

