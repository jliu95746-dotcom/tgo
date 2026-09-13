import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '@/stores';
import type { WuKongIMMessage, Message } from '@/types';
import { getChannelKey } from '@/utils/channelUtils';
import { WuKongIMUtils } from '@/services/wukongimApi';

// Stable empty array to prevent infinite loops
const EMPTY_MESSAGES: WuKongIMMessage[] = [];

/**
 * Custom hook for managing historical messages from WuKongIM
 * Provides stable selectors and loading logic to prevent infinite loops
 */
export interface UseHistoricalMessagesProps {
  channelId?: string;
  channelType?: number;
}

export interface UseHistoricalMessagesReturn {
  // Data
  historicalMessages: WuKongIMMessage[];
  hasMoreHistory: boolean;
  
  // Loading states
  isLoadingHistory: boolean;
  isLoadingMore: boolean;
  historyError: string | null;
  
  // Actions
  loadMoreHistory: () => Promise<void>;
  retryLoadHistory: () => void;
  
  // Utilities
  convertWuKongIMToMessage: (wkMessage: WuKongIMMessage) => Message;
}

/**
 * Hook for managing WuKongIM historical messages with stable selectors
 */
export const useHistoricalMessages = ({
  channelId,
  channelType
}: UseHistoricalMessagesProps): UseHistoricalMessagesReturn => {
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // Create stable selectors using useCallback to prevent infinite loops
  const historicalMessagesSelector = useCallback((state: any): WuKongIMMessage[] => {
    if (!channelId || channelType == null) return EMPTY_MESSAGES;
    const key = getChannelKey(channelId, channelType);
    return state.historicalMessages[key] || EMPTY_MESSAGES;
  }, [channelId, channelType]);

  const hasMoreHistorySelector = useCallback((state: any): boolean => {
    if (!channelId || channelType == null) return false;
    const key = getChannelKey(channelId, channelType);
    return state.hasMoreHistory[key] || false;
  }, [channelId, channelType]);

  // Use the stable selectors
  const historicalMessages = useChatStore(historicalMessagesSelector);
  const hasMoreHistory = useChatStore(hasMoreHistorySelector);

  // Simple selectors for primitive values (these are stable)
  const isLoadingHistory = useChatStore((state) => state.isLoadingHistory);
  const historyError = useChatStore((state) => state.historyError);
  const loadHistoricalMessagesAction = useChatStore((state) => state.loadHistoricalMessages);
  const loadMoreHistoryAction = useChatStore((state) => state.loadMoreHistory);
  const setHistoryError = useChatStore((state) => state.setHistoryError);

  // Use unified conversion from WuKongIMUtils (stable reference)
  const convertWuKongIMToMessage = WuKongIMUtils.convertToMessage;

  const initialHistoryRequestKey = useRef<string | null>(null);

  // Load once per selected channel; empty results and failures require explicit retry.
  useEffect(() => {
    if (!channelId || channelType == null) {
      initialHistoryRequestKey.current = null;
      return;
    }
    const key = getChannelKey(channelId, channelType);
    if (initialHistoryRequestKey.current === key) return;
    if (historicalMessages.length > 0) {
      initialHistoryRequestKey.current = key;
      return;
    }
    if (isLoadingHistory) return;
    initialHistoryRequestKey.current = key;
    loadHistoricalMessagesAction(channelId, channelType).catch(error => {
      console.error('Failed to load initial history:', error);
    });
  }, [channelId, channelType, historicalMessages.length, isLoadingHistory, loadHistoricalMessagesAction]);

  // Memoized load more function
  const loadMoreHistory = useCallback(async (): Promise<void> => {
    if (!channelId || channelType == null || isLoadingHistory || isLoadingMore || !hasMoreHistory) {
      return;
    }

    setIsLoadingMore(true);
    try {
      await loadMoreHistoryAction(channelId, channelType);
    } catch (error) {
      console.error('Failed to load more messages:', error);
    } finally {
      setIsLoadingMore(false);
    }
  }, [channelId, channelType, isLoadingHistory, isLoadingMore, hasMoreHistory, loadMoreHistoryAction]);

  // Memoized retry function
  const retryLoadHistory = useCallback(() => {
    if (channelId && channelType != null) {
      setHistoryError(null);
      loadHistoricalMessagesAction(channelId, channelType);
    }
  }, [channelId, channelType, setHistoryError, loadHistoricalMessagesAction]);

  return {
    // Data
    historicalMessages,
    hasMoreHistory,
    
    // Loading states
    isLoadingHistory,
    isLoadingMore,
    historyError,
    
    // Actions
    loadMoreHistory,
    retryLoadHistory,
    
    // Utilities
    convertWuKongIMToMessage
  };
};
