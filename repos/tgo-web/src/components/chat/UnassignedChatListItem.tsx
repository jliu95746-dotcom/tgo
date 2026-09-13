import React, { useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { PlatformType, type Chat, type ChannelVisitorExtra } from '@/types';
import { DEFAULT_CHANNEL_TYPE } from '@/constants';
import { useChannelDisplay } from '@/hooks/useChannelDisplay';
import { ChatAvatar } from './ChatAvatar';
import { ChatPlatformIcon } from './ChatPlatformIcon';
import { toPlatformType } from '@/utils/platformUtils';
import { formatChatLastMessage } from '@/utils/messageFormatting';
import { Clock } from 'lucide-react';

export interface UnassignedChatListItemProps {
  chat: Chat;
  isActive: boolean;
  onClick: (chat: Chat) => void;
}

/**
 * Get urgency level based on waiting time
 * @param timestampSec - Unix timestamp in seconds
 * @returns 'normal' | 'warning' | 'urgent'
 */
const getUrgencyLevel = (timestampSec: number | undefined): 'normal' | 'warning' | 'urgent' => {
  if (!timestampSec) return 'normal';
  
  const now = Math.floor(Date.now() / 1000);
  const diffSeconds = now - timestampSec;
  
  if (diffSeconds >= 600) { // >= 10 minutes
    return 'urgent';
  } else if (diffSeconds >= 180) { // >= 3 minutes
    return 'warning';
  }
  return 'normal';
};

/**
 * Individual chat list item for unassigned/waiting conversations.
 */
export const UnassignedChatListItem: React.FC<UnassignedChatListItemProps> = React.memo(({ chat, isActive, onClick }) => {
  const { t } = useTranslation();
  const channelId = chat.channelId;
  const channelType = chat.channelType ?? DEFAULT_CHANNEL_TYPE;

  // Use unified hook for channel display info
  const { name, avatar, extra } = useChannelDisplay({
    channelId,
    channelType,
  });

  const displayName = name;
  const displayAvatar = avatar;

  const parseMinutesAgo = useCallback((timestamp?: string): number | undefined => {
    if (!timestamp) return undefined;
    // Normalize: replace space with T, limit fractional seconds to 3 digits
    let normalized = timestamp.replace(' ', 'T').replace(/(\.\d{3})\d+$/, '$1');
    // If no timezone info, assume UTC and append 'Z'
    if (!/[Zz]$/.test(normalized) && !/[+-]\d{2}:\d{2}$/.test(normalized)) {
      normalized += 'Z';
    }
    let ms = Date.parse(normalized);
    if (!Number.isFinite(ms)) {
      // Fallback: drop fractional seconds entirely, still assume UTC
      normalized = `${timestamp.replace(' ', 'T').split('.')[0]  }Z`;
      ms = Date.parse(normalized);
    }
    if (!Number.isFinite(ms)) return undefined;
    const minutes = Math.floor((Date.now() - ms) / 60000);
    return Math.max(0, minutes);
  }, []);

  // Get online status from extra (channel info)
  const visitorExtra = extra as ChannelVisitorExtra | undefined;
  const { visitorStatus, lastSeenMinutes } = useMemo((): { visitorStatus?: 'online' | 'offline' | 'away'; lastSeenMinutes?: number } => {
    // Priority: extra.is_online (from channel info API) > chat.visitorStatus (legacy)
    if (visitorExtra?.is_online !== undefined) {
      if (visitorExtra.is_online) {
        return { visitorStatus: 'online', lastSeenMinutes: undefined };
      } else {
        // Calculate lastSeenMinutes from last_offline_time if available
        const minutes = parseMinutesAgo(visitorExtra.last_offline_time) ?? 0;
        return { visitorStatus: 'offline', lastSeenMinutes: minutes };
      }
    }
    // Fallback to chat.visitorStatus
    return { visitorStatus: chat.visitorStatus, lastSeenMinutes: chat.lastSeenMinutes };
  }, [visitorExtra?.is_online, visitorExtra?.last_offline_time, chat.visitorStatus, chat.lastSeenMinutes, parseMinutesAgo]);

  // Handle click - no unread clearing for unassigned chats
  const handleClick = useCallback(() => { 
    onClick(chat); 
  }, [onClick, chat]);

  /**
   * Format waiting time duration with i18n
   */
  const waitingTime = useMemo(() => {
    const timestampSec = chat.lastTimestampSec;
    if (!timestampSec) return t('chat.list.waiting.justNow', '刚刚');
    
    const now = Math.floor(Date.now() / 1000);
    const diffSeconds = now - timestampSec;
    
    if (diffSeconds < 60) {
      return t('chat.list.waiting.justNow', '刚刚');
    } else if (diffSeconds < 3600) {
      const minutes = Math.floor(diffSeconds / 60);
      return t('chat.list.waiting.minutes', { count: minutes, defaultValue: `${minutes}分钟` });
    } else if (diffSeconds < 86400) {
      const hours = Math.floor(diffSeconds / 3600);
      const minutes = Math.floor((diffSeconds % 3600) / 60);
      return minutes > 0 
        ? t('chat.list.waiting.hoursMinutes', { h: hours, m: minutes, defaultValue: `${hours}小时${minutes}分` })
        : t('chat.list.waiting.hours', { count: hours, defaultValue: `${hours}小时` });
    } else {
      const days = Math.floor(diffSeconds / 86400);
      const hours = Math.floor((diffSeconds % 86400) / 3600);
      return hours > 0 
        ? t('chat.list.waiting.daysHours', { d: days, h: hours, defaultValue: `${days}天${hours}小时` })
        : t('chat.list.waiting.days', { count: days, defaultValue: `${days}天` });
    }
  }, [chat.lastTimestampSec, t]);

  const urgencyLevel = useMemo(() => getUrgencyLevel(chat.lastTimestampSec), [chat.lastTimestampSec]);

  // Urgency-based styling
  const urgencyStyles = useMemo(() => {
    if (isActive) {
      return {
        badge: 'bg-white/20 text-white',
        icon: 'text-white',
        text: 'text-white',
      };
    }
    switch (urgencyLevel) {
      case 'urgent':
        return {
          badge: 'bg-red-100 dark:bg-red-900/30',
          icon: 'text-red-500 dark:text-red-400',
          text: 'text-red-600 dark:text-red-400 font-semibold',
        };
      case 'warning':
        return {
          badge: 'bg-orange-100 dark:bg-orange-900/30',
          icon: 'text-orange-500 dark:text-orange-400',
          text: 'text-orange-600 dark:text-orange-400 font-medium',
        };
      default:
        return {
          badge: 'bg-gray-100 dark:bg-gray-700',
          icon: 'text-gray-400 dark:text-gray-500',
          text: 'text-gray-500 dark:text-gray-400',
        };
    }
  }, [isActive, urgencyLevel]);

  return (
    <div
      className={`
        flex items-center p-3 rounded-lg cursor-pointer transition-colors duration-150
        ${isActive ? 'bg-blue-500 dark:bg-blue-600 text-white shadow-sm' : 'hover:bg-gray-100/70 dark:hover:bg-gray-700/70'}
      `}
      onClick={handleClick}
    >
      <ChatAvatar
        displayName={displayName}
        displayAvatar={displayAvatar}
        visitorStatus={visitorStatus}
        lastSeenMinutes={lastSeenMinutes}
        colorSeed={channelId}
      />

      <div className="flex-grow overflow-hidden">
        {/* First row: Name and Platform Icon */}
        <div className="flex justify-between items-center">
          <h3 className={`text-sm font-semibold truncate flex items-center ${isActive ? 'text-white' : 'text-gray-800 dark:text-gray-200'}`}>
            <span className="truncate">{displayName}</span>
            <ChatPlatformIcon platformType={(() => {
              const extraObj: any = extra;
              const fromExtra: PlatformType | undefined = (extraObj && typeof extraObj === 'object' && 'platform_type' in extraObj)
                ? (extraObj.platform_type as PlatformType)
                : undefined;
              return fromExtra ?? toPlatformType(chat.platform);
            })()} />
          </h3>
          {/* Waiting time badge - prominently displayed */}
          <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs flex-shrink-0 ml-2 ${urgencyStyles.badge}`}>
            <Clock className={`w-3 h-3 ${urgencyStyles.icon}`} />
            <span className={urgencyStyles.text}>
              {waitingTime}
            </span>
          </div>
        </div>

        {/* Second row: Last message preview + "等待接入" badge (in place of unread count) */}
        <div className="flex justify-between items-center mt-1">
          <p className={`text-xs truncate flex-1 ${isActive ? 'text-blue-100' : 'text-gray-500 dark:text-gray-400'}`}>
            {formatChatLastMessage(chat, t)}
          </p>
          {/* Status indicator - in place of unread badge */}
          <span className={`
            text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 ml-2
            ${isActive 
              ? 'bg-white/20 text-white' 
              : 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
            }
          `}>
            {t('chat.list.waiting.status', '等待接入')}
          </span>
        </div>
      </div>
    </div>
  );
});

UnassignedChatListItem.displayName = 'UnassignedChatListItem';
