/**
 * Time Formatting Utilities
 * Provides consistent, localized time formatting across the application
 */

import i18n from '@/i18n';
import { parseAPITimestampToLocalDate } from './dateUtils';

/**
 * Format timestamp to localized date and time (up to minutes precision)
 * @param timestamp - ISO timestamp string or Date object
 * @param options - Formatting options
 * @returns Formatted time string in user's local timezone
 */
export const formatDateTime = (
  timestamp: string | Date,
  options: {
    includeDate?: boolean;
    includeTime?: boolean;
    locale?: string;
  } = {}
): string => {
  const {
    includeDate = true,
    includeTime = true,
    locale
  } = options;
  const fmtLocale = locale ?? (typeof i18n?.language === 'string' ? i18n.language : 'zh-CN');

  try {
    const date = typeof timestamp === 'string' ? parseAPITimestampToLocalDate(timestamp) || new Date(timestamp) : timestamp;

    // Check if date is valid
    if (isNaN(date.getTime())) {
      console.warn('Invalid timestamp:', timestamp);
      return formatDateTime(new Date(), options); // Fallback to current time
    }

    // Format options for Intl.DateTimeFormat
    const formatOptions: Intl.DateTimeFormatOptions = {};

    if (includeDate) {
      formatOptions.year = 'numeric';
      formatOptions.month = '2-digit';
      formatOptions.day = '2-digit';
    }

    if (includeTime) {
      formatOptions.hour = '2-digit';
      formatOptions.minute = '2-digit';
      formatOptions.hour12 = false; // Use 24-hour format
    }

    // Use Intl.DateTimeFormat for proper localization
    const formatter = new Intl.DateTimeFormat(fmtLocale, formatOptions);
    return formatter.format(date);

  } catch (error) {
    console.error('Error formatting timestamp:', error, 'timestamp:', timestamp);
    // Fallback to current time
    return formatDateTime(new Date(), options);
  }
};

/**
 * Format timestamp for knowledge base "最近更新" display
 * Shows date and time up to minutes precision in user's local timezone
 * @param timestamp - ISO timestamp string or Date object
 * @returns Formatted string like "2024-01-15 14:30"
 */
export const formatKnowledgeBaseUpdatedTime = (timestamp: string | Date): string => {
  return formatDateTime(timestamp, {
    includeDate: true,
    includeTime: true,
    locale: i18n.language
  });
};

/**
 * Format timestamp for file upload display
 * @param timestamp - ISO timestamp string or Date object
 * @returns Formatted string in Chinese locale
 */
export const formatFileUploadTime = (timestamp: string | Date): string => {
  return formatDateTime(timestamp, {
    includeDate: true,
    includeTime: true,
    locale: i18n.language
  });
};

/**
 * Format timestamp to show only time (HH:MM format)
 * @param timestamp - ISO timestamp string or Date object
 * @returns Time string like "14:30"
 */
export const formatTimeOnly = (timestamp: string | Date): string => {
  return formatDateTime(timestamp, {
    includeDate: false,
    includeTime: true,
    locale: i18n.language
  });
};

/**
 * Format timestamp to show only date
 * @param timestamp - ISO timestamp string or Date object
 * @returns Date string like "2024-01-15"
 */
export const formatDateOnly = (timestamp: string | Date): string => {
  return formatDateTime(timestamp, {
    includeDate: true,
    includeTime: false,
    locale: i18n.language
  });
};

/**
 * Format timestamp for relative time display (e.g., "2 hours ago")
 * @param timestamp - ISO timestamp string or Date object
 * @returns Relative time string in Chinese
 */
export const formatRelativeTime = (timestamp: string | Date): string => {
  try {
    const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp;
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();

    // If invalid date, return formatted current time
    if (isNaN(date.getTime())) {
      return formatKnowledgeBaseUpdatedTime(now);
    }

    const diffMinutes = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    // If less than 1 minute ago
    if (diffMinutes < 1) {
      return i18n.t('time.relative.justNow', { defaultValue: '刚刚' });
    }

    // If less than 1 hour ago
    if (diffMinutes < 60) {
      return i18n.t('time.relative.minutes', { count: diffMinutes, defaultValue: `${diffMinutes}分钟前` });
    }

    // If less than 24 hours ago
    if (diffHours < 24) {
      return i18n.t('time.relative.hours', { count: diffHours, defaultValue: `${diffHours}小时前` });
    }

    // If less than 7 days ago
    if (diffDays < 7) {
      return i18n.t('time.relative.days', { count: diffDays, defaultValue: `${diffDays}天前` });
    }

    // For older dates, show the actual date and time
    return formatKnowledgeBaseUpdatedTime(date);

  } catch (error) {
    console.error('Error formatting relative time:', error);
    return formatKnowledgeBaseUpdatedTime(new Date());
  }
};

/**
 * Check if a timestamp is today
 * @param timestamp - ISO timestamp string or Date object
 * @returns True if the timestamp is today
 */
export const isToday = (timestamp: string | Date): boolean => {
  try {
    const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp;
    const today = new Date();

    return date.getDate() === today.getDate() &&
           date.getMonth() === today.getMonth() &&
           date.getFullYear() === today.getFullYear();
  } catch {
    return false;
  }
};

/**
 * Check if a timestamp is yesterday
 * @param timestamp - ISO timestamp string or Date object
 * @returns True if the timestamp is yesterday
 */
export const isYesterday = (timestamp: string | Date): boolean => {
  try {
    const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp;
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);

    return date.getDate() === yesterday.getDate() &&
           date.getMonth() === yesterday.getMonth() &&
           date.getFullYear() === yesterday.getFullYear();
  } catch {
    return false;
  }
};

/**
 * Smart time formatting that shows different formats based on recency
 * @param timestamp - ISO timestamp string or Date object
 * @returns Smart formatted time string
 */
export const formatSmartTime = (timestamp: string | Date): string => {
  try {
    const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp;

    const todayLabel = i18n.t('time.today', { defaultValue: '今天' });
    const yesterdayLabel = i18n.t('time.yesterday', { defaultValue: '昨天' });
    if (isToday(date)) {
      return `${todayLabel} ${formatTimeOnly(date)}`;
    } else if (isYesterday(date)) {
      return `${yesterdayLabel} ${formatTimeOnly(date)}`;
    } else {
      return formatKnowledgeBaseUpdatedTime(date);
    }
  } catch (error) {
    console.error('Error in smart time formatting:', error);
    return formatKnowledgeBaseUpdatedTime(new Date());
  }
};

/**
 * Validate and normalize timestamp
 * @param timestamp - ISO timestamp string or Date object
 * @returns Valid Date object or current date as fallback
 */
export const normalizeTimestamp = (timestamp: string | Date): Date => {
  try {
    const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp;

    if (isNaN(date.getTime())) {
      console.warn('Invalid timestamp, using current date:', timestamp);
      return new Date();
    }

    return date;
  } catch (error) {
    console.error('Error normalizing timestamp:', error);
    return new Date();
  }
};

/**
 * Get current timestamp in ISO format
 * @returns Current timestamp as ISO string
 */
export const getCurrentTimestamp = (): string => {
  return new Date().toISOString();
};


/**
 * Format timestamp according to WeChat conversation list rules
 * - Today: HH:mm
 * - Yesterday: 昨天 HH:mm
 * - 2-6 days ago: 星期一..星期日
 * - 7+ days ago: M月D日
 */
export const formatWeChatConversationTime = (
  timestamp: string | Date | number
): string => {
  try {
    let date: Date;
    if (typeof timestamp === 'string' || typeof timestamp === 'number') {
      date = typeof timestamp === 'string' 
        ? parseAPITimestampToLocalDate(timestamp) || new Date(timestamp)
        : new Date(timestamp);
      
      // Handle strings like "HH:MM" by assuming today at that time
      if (typeof timestamp === 'string' && isNaN(date.getTime())) {
        const match = timestamp.trim().match(/^(\d{1,2}):(\d{2})/);
        if (match) {
          const now = new Date();
          const hours = Math.max(0, Math.min(23, parseInt(match[1], 10)));
          const minutes = Math.max(0, Math.min(59, parseInt(match[2], 10)));
          date = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hours, minutes);
        }
      }
    } else {
      date = timestamp;
    }

    if (isNaN(date.getTime())) {
      // Fallback to showing current time if invalid
      return formatTimeOnly(new Date());
    }

    const now = new Date();
    const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const todayStart = startOf(now);
    const inputStart = startOf(date);

    // Difference in calendar days
    const diffDays = Math.floor((todayStart.getTime() - inputStart.getTime()) / (24 * 60 * 60 * 1000));

    // Future-dated or today -> show time only
    if (diffDays <= 0) {
      return formatTimeOnly(date);
    }

    // Yesterday
    if (diffDays === 1) {
      return `${i18n.t('time.yesterday', { defaultValue: '昨天' })} ${formatTimeOnly(date)}`;
    }

    // Within past week (2-6 days): show weekday name
    if (diffDays >= 2 && diffDays <= 6) {
      const weekdayIndex = date.getDay();
      return i18n.t(`time.weekdays.${weekdayIndex}`, {
        defaultValue: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][weekdayIndex]
      });
    }

    // Older than one week: localized month/day
    return date.toLocaleDateString(i18n.language, { month: 'numeric', day: 'numeric' });
  } catch {
    // Graceful fallback
    return formatTimeOnly(new Date());
  }
};
