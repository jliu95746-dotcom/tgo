import { useCallback, useEffect, useMemo } from 'react';
import { useChannelStore } from '@/stores/channelStore';
import type { ChannelInfo, ChannelExtra } from '@/types';
import { getChannelKey } from '@/utils/channelUtils';
import { generateDefaultAvatar, hasValidAvatar, type DefaultAvatar } from '@/utils/avatarUtils';
import { resolveDisplayChannel, resolveDisplayProfile } from '@/utils/channelDisplayIdentity';

/**
 * Props for useChannelDisplay hook
 */
export interface UseChannelDisplayProps {
  channelId?: string;
  channelType?: number;
  /** Optional override for name (e.g., from message.fromInfo) */
  overrideName?: string;
  /** Optional override for avatar (e.g., from message.fromInfo) */
  overrideAvatar?: string;
  /** Whether to skip fetching channel info (e.g., for own messages) */
  skipFetch?: boolean;
}

/**
 * Return value of useChannelDisplay hook
 */
export interface UseChannelDisplayReturn {
  /** Display name (resolved or fallback) */
  name: string;
  /** Avatar URL */
  avatar: string;
  /** Default avatar info when no valid avatar */
  defaultAvatar: DefaultAvatar | null;
  /** Whether has a valid avatar URL */
  hasValidAvatar: boolean;
  /** Whether channel info is being loaded */
  isLoading: boolean;
  /** Full channel info from store */
  channelInfo: ChannelInfo | undefined;
  /** Extra info from channel (contains platform_type, is_online, etc.) */
  extra: ChannelExtra | undefined;
}

/**
 * Unified hook for managing channel display information (name, avatar)
 * 
 * Features:
 * - Fetches channel info from channelStore cache
 * - Auto-triggers ensureChannel for missing info
 * - Provides fallback display values
 * - Generates default avatar when no valid avatar
 * - Supports override values (e.g., from message.fromInfo)
 * 
 * @example
 * // Basic usage in ChatListItem
 * const { name, avatar, defaultAvatar, hasValidAvatar } = useChannelDisplay({
 *   channelId: chat.channelId,
 *   channelType: chat.channelType,
 * });
 * 
 * @example
 * // With override in ChatMessage
 * const { name, avatar } = useChannelDisplay({
 *   channelId: message.channelId,
 *   channelType: message.channelType,
 *   overrideName: message.fromInfo?.name,
 *   overrideAvatar: message.fromInfo?.avatar,
 *   skipFetch: isOwnMessage,
 * });
 */
export const useChannelDisplay = ({
  channelId: requestedChannelId,
  channelType: requestedChannelType,
  overrideName,
  overrideAvatar,
  skipFetch = false,
}: UseChannelDisplayProps): UseChannelDisplayReturn => {
  const { channelId, channelType } = resolveDisplayChannel(requestedChannelId, requestedChannelType);
  // Create stable selector for channel info
  // This ensures component re-renders when channels[key] changes
  const channelInfoSelector = useCallback(
    (state: ReturnType<typeof useChannelStore.getState>) => {
      if (!channelId || channelType == null) return undefined;
      const key = getChannelKey(channelId, channelType);
      return state.channels[key];
    },
    [channelId, channelType]
  );

  // Subscribe to channel info - will trigger re-render when data is fetched
  const channelInfo = useChannelStore(channelInfoSelector);

  // Subscribe to loading state
  const isLoading = useChannelStore(
    useCallback(
      (state) => {
        if (!channelId || channelType == null) return false;
        const key = getChannelKey(channelId, channelType);
        return Boolean(state.inFlight[key]);
      },
      [channelId, channelType]
    )
  );

  // Subscribe to error state
  const hasError = useChannelStore(
    useCallback(
      (state) => {
        if (!channelId || channelType == null) return false;
        const key = getChannelKey(channelId, channelType);
        return Boolean(state.errors[key]);
      },
      [channelId, channelType]
    )
  );

  // Get ensureChannel action
  const ensureChannel = useChannelStore((state) => state.ensureChannel);

  // Trigger fetch if needed
  useEffect(() => {

    if (skipFetch) return;
    if (!channelId || channelType == null) return;
    if (channelInfo || isLoading || hasError) return;


    ensureChannel({ channel_id: channelId, channel_type: channelType }).catch(() => {
      // Error is stored in channelStore.errors, no need to handle here
    });
  }, [channelId, channelType, channelInfo, isLoading, hasError, skipFetch, ensureChannel]);

  // Visitor messages and the list share the saved profile; staff retain overrides.
  const profile = useMemo(() => resolveDisplayProfile(
    channelId,
    channelInfo,
    overrideName,
    overrideAvatar && hasValidAvatar(overrideAvatar) ? overrideAvatar : undefined,
  ), [channelId, channelInfo, overrideName, overrideAvatar]);
  const name = useMemo(() => {
    if (profile.name) return profile.name;
    return `访客${(channelId || '').replace(/-vtr$/, '').slice(-4)}`;
  }, [profile.name, channelId]);

  const avatar = profile.avatar || '';

  const validAvatar = hasValidAvatar(avatar);

  const defaultAvatar = useMemo(() => {
    if (validAvatar) return null;
    // Use channelId as colorSeed for consistent avatar color
    return generateDefaultAvatar(name, channelId);
  }, [validAvatar, name, channelId]);

  const extra = channelInfo?.extra;

  return {
    name,
    avatar,
    defaultAvatar,
    hasValidAvatar: validAvatar,
    isLoading,
    channelInfo,
    extra,
  };
};
