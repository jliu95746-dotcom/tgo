import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ChatList from '../components/layout/ChatList';
import ChatWindow from '../components/layout/ChatWindow';
import VisitorPanel from '../components/layout/VisitorPanel';
import { useChatStore, chatSelectors } from '@/stores';
import { useChannelStore } from '@/stores/channelStore';
import { getChannelKey } from '@/utils/channelUtils';
import type { ChatTabType } from '@/components/chat/ChatListTabs';
import type { ChannelVisitorExtra } from '@/types';

/**
 * Chat page component - contains the original chat interface
 */
interface ChatPageLocationState {
  agentName?: string;
  agentAvatar?: string;
  platform?: string;
}

const ChatPage: React.FC = () => {
  const { t } = useTranslation();
  const { channelType: urlChannelType, channelId: urlChannelId } = useParams<{ channelType: string; channelId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const locationState = location.state as ChatPageLocationState | null;
  
  // Tab state management
  const [activeTab, setActiveTab] = useState<ChatTabType>('mine');
  const [isCompactListOpen, setIsCompactListOpen] = useState(true);
  const [isVisitorDrawerOpen, setIsVisitorDrawerOpen] = useState(false);
  const [visibleConversationCount, setVisibleConversationCount] = useState(0);
  
  // Refresh trigger for ChatList (increment to trigger refresh)
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  
  // Deleted chat channel (to notify ChatList to remove from local state)
  const [deletedChatChannel, setDeletedChatChannel] = useState<{ channelId: string; channelType: number } | null>(null);
  
  // Callback for when a visitor is accepted - switch to "mine" tab and refresh lists
  const handleAcceptVisitor = useCallback(() => {
    setActiveTab('mine');
    // Trigger refresh of chat lists
    setRefreshTrigger(prev => prev + 1);
  }, []);
  
  // Helper to check if we should clear unread for a channel
  // Don't clear unread if service_status is 'queued'
  const shouldClearUnreadForChannel = useCallback((channelId: string, channelType: number): boolean => {
    const channelStore = useChannelStore.getState();
    const channelInfo = channelStore.getChannel(channelId, channelType);
    const extra = channelInfo?.extra as ChannelVisitorExtra | undefined;
    const serviceStatus = extra?.service_status;
    // Don't clear unread if visitor is in queued or new status
    return serviceStatus !== 'queued' && serviceStatus !== 'new';
  }, []);
  
  const activeChat = useChatStore(chatSelectors.activeChat);
  const setActiveChat = useChatStore(state => state.setActiveChat);
  const chats = useChatStore(state => state.chats);
  const loadHistoricalMessages = useChatStore(state => state.loadHistoricalMessages);
  const clearConversationUnread = useChatStore(state => state.clearConversationUnread);
  
  // Callback for when a chat is ended - remove from list and select the next chat
  const handleEndChatSuccess = useCallback((endedChannelId: string, endedChannelType: number) => {
    // Notify ChatList to remove the chat from local state
    setDeletedChatChannel({ channelId: endedChannelId, channelType: endedChannelType });
    
    // Clear the deleted channel after a short delay (to allow ChatList to process)
    setTimeout(() => setDeletedChatChannel(null), 100);
  }, []);

  // Track if we're syncing from URL to prevent loops
  const isSyncingFromUrl = useRef(false);
  // Track if initial URL sync has been attempted
  const hasAttemptedUrlSync = useRef(false);

  // Note: 会话列表的加载由 ChatList.tsx 根据当前 tab 处理，不再需要在这里调用 syncConversationsIfNeeded

  const createChatByChannel = useChatStore(state => state.createChatByChannel);

  // 从 URL 参数定位会话（仅在 URL 变化时执行一次）
  useEffect(() => {
    // Only attempt URL sync once per URL change
    if (urlChannelType && urlChannelId && !hasAttemptedUrlSync.current) {
      hasAttemptedUrlSync.current = true;
      const targetChannelType = parseInt(urlChannelType, 10);
      
      // 使用 getState() 获取最新的 chats，避免依赖 chats 数组导致重复触发
      const currentChats = useChatStore.getState().chats;
      const currentActiveChat = useChatStore.getState().activeChat;
      
      // 如果当前 activeChat 已经是目标会话，不需要再设置
      if (currentActiveChat?.channelId === urlChannelId && currentActiveChat?.channelType === targetChannelType) {
        return;
      }
      
      const targetChat = currentChats.find(
        c => c.channelId === urlChannelId && c.channelType === targetChannelType
      );
      
      if (targetChat) {
        // Found the chat, select it
        isSyncingFromUrl.current = true;
        setActiveChat(targetChat);
        loadHistoricalMessages(targetChat.channelId, targetChat.channelType);
        // Clear unread (but not if service_status is 'queued')
        if ((targetChat.unreadCount || 0) > 0 && shouldClearUnreadForChannel(targetChat.channelId, targetChat.channelType)) {
          clearConversationUnread(targetChat.channelId, targetChat.channelType);
        }
        isSyncingFromUrl.current = false;
      } else {
        // Chat not found, create a new one
        isSyncingFromUrl.current = true;
        const newChat = createChatByChannel(urlChannelId, targetChannelType, {
          platform: locationState?.platform,
          name: locationState?.agentName,
          avatar: locationState?.agentAvatar
        });
        setActiveChat(newChat);
        loadHistoricalMessages(urlChannelId, targetChannelType);
        isSyncingFromUrl.current = false;
      }
    }
  }, [urlChannelType, urlChannelId, setActiveChat, loadHistoricalMessages, clearConversationUnread, createChatByChannel, locationState, shouldClearUnreadForChannel]);

  // Reset URL sync flag when URL params change
  useEffect(() => {
    hasAttemptedUrlSync.current = false;
  }, [urlChannelType, urlChannelId]);

  // 设置默认活跃聊天（仅当没有 URL 参数时）
  useEffect(() => {
    // If URL has params, don't auto-select first chat
    if (urlChannelType && urlChannelId) return;
    
    if (!activeChat && chats.length > 0) {
      const firstChat = chats[0];
      setActiveChat(firstChat);
      // Update URL for the default chat
      navigate(`/chat/${firstChat.channelType}/${firstChat.channelId}`, { replace: true });
    }
  }, [activeChat, chats, setActiveChat, urlChannelType, urlChannelId, navigate]);

  const handleChatSelect = (chat: any): void => {
    const prev = activeChat;
    
    // Don't clear unread for unassigned tab (no API call)
    const isUnassignedTab = activeTab === 'unassigned';

    // Clear unread for the conversation we're leaving (if different)
    // Use activeChat's unreadCount directly since chats store might not be in sync
    if (!isUnassignedTab && prev && !(prev.channelId === chat.channelId && prev.channelType === chat.channelType)) {
      if ((prev.unreadCount || 0) > 0 && shouldClearUnreadForChannel(prev.channelId, prev.channelType)) {
        clearConversationUnread(prev.channelId, prev.channelType);
      }
    }

    // Clear unread for the clicked/active conversation
    // Use the chat object directly (passed from ChatList) since it has the actual unreadCount
    if (!isUnassignedTab) {
      const unreadCount = chat.unreadCount || 0;
      const shouldClear = shouldClearUnreadForChannel(chat.channelId, chat.channelType);
      console.log('🔔 handleChatSelect: Clear unread check', {
        channelId: chat.channelId,
        channelType: chat.channelType,
        unreadCount,
        shouldClear,
        isUnassignedTab,
      });
      if (unreadCount > 0 && shouldClear) {
        console.log('🔔 handleChatSelect: Calling clearConversationUnread');
        clearConversationUnread(chat.channelId, chat.channelType);
      }
    }
    
    // For unassigned tab, force refresh channel info
    // This ensures we get the latest visitor info when selecting an unassigned conversation
    if (isUnassignedTab && chat.channelId && chat.channelType != null) {
      const channelStore = useChannelStore.getState();
      channelStore.refreshChannel({ channel_id: chat.channelId, channel_type: chat.channelType });
    }

    setActiveChat(chat);
    if (window.innerWidth < 900) {
      setIsCompactListOpen(false);
    }
    setIsVisitorDrawerOpen(false);
    
    // Update URL with the selected chat's channel info
    if (chat.channelId && chat.channelType != null) {
      navigate(`/chat/${chat.channelType}/${chat.channelId}`, { replace: true });
      loadHistoricalMessages(chat.channelId, chat.channelType);
    }
  };


  // When returning focus to the tab/window, clear unread for the currently open conversation
  useEffect(() => {
    const onFocus = () => {
      const { activeChat: cur, clearConversationUnread: clearFn } = useChatStore.getState() as any;
      if (cur?.channelId && cur.channelType != null) {
        // Use activeChat's unreadCount directly
        if ((cur.unreadCount || 0) > 0) {
          // Check if we should clear unread (not if service_status is 'queued')
          const channelStore = useChannelStore.getState();
          const channelInfo = channelStore.getChannel(cur.channelId, cur.channelType);
          const extra = channelInfo?.extra as ChannelVisitorExtra | undefined;
          const serviceStatus = extra?.service_status;
          if (serviceStatus !== 'queued' && serviceStatus !== 'new') {
            clearFn(cur.channelId, cur.channelType);
          }
        }
      }
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);

  // 判断当前会话是否是 agent 会话（channelId 以 -agent 结尾）
  const isAgentChat = activeChat?.channelId?.endsWith('-agent') ?? false;
  const isAIChat = isAgentChat;

  useEffect(() => {
    if (!activeChat || isAIChat) {
      setIsVisitorDrawerOpen(false);
    }
  }, [activeChat, isAIChat]);

  useEffect(() => {
    if (activeChat && window.innerWidth < 900) {
      setIsCompactListOpen(false);
    }
  }, [activeChat]);

  useEffect(() => {
    if (!isVisitorDrawerOpen) return;

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsVisitorDrawerOpen(false);
      }
    };

    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [isVisitorDrawerOpen]);

  return (
    <div className="relative flex h-full min-w-0 flex-1 bg-gray-50 dark:bg-gray-900">
      {/* Chat List */}
      <div className={`h-full shrink-0 ${isCompactListOpen ? 'max-[899px]:flex max-[899px]:w-full' : 'max-[899px]:hidden'}`}>
        <ChatList
          activeChat={activeChat ?? undefined}
          onChatSelect={handleChatSelect}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          refreshTrigger={refreshTrigger}
          deletedChatChannel={deletedChatChannel}
          className="max-[899px]:w-full"
          onVisibleItemCountChange={setVisibleConversationCount}
        />
      </div>

      {/* Main Chat Window */}
      <div className={`min-w-0 flex-1 ${isCompactListOpen ? 'max-[899px]:hidden' : 'max-[899px]:flex'}`}>
        <ChatWindow
          key={activeChat ? getChannelKey(activeChat.channelId, activeChat.channelType) : 'no-active'}
          activeChat={activeChat ?? undefined}
          onAcceptVisitor={handleAcceptVisitor}
          onEndChatSuccess={handleEndChatSuccess}
          onBackToList={() => setIsCompactListOpen(true)}
          onOpenVisitorPanel={() => setIsVisitorDrawerOpen(true)}
          hasConversations={visibleConversationCount > 0}
        />
      </div>

      {/* Wide-screen visitor panel - only render when a visitor conversation is selected */}
      {!isAIChat && activeChat && (
        <div className="hidden min-[1200px]:flex">
          <VisitorPanel activeChat={activeChat} />
        </div>
      )}

      {/* Visitor details become a drawer when the workspace is narrower than 1200px */}
      {!isAIChat && activeChat && isVisitorDrawerOpen && (
        <div className="fixed inset-0 z-[100] min-[1200px]:hidden">
          <button
            type="button"
            aria-label={t('visitor.ui.closePanel', '关闭访客信息')}
            className="absolute inset-0 bg-gray-950/30 backdrop-blur-[1px]"
            onClick={() => setIsVisitorDrawerOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-labelledby="visitor-drawer-title"
            className="absolute inset-y-0 right-0 flex w-[min(360px,calc(100vw-24px))] flex-col bg-white shadow-2xl dark:bg-gray-800"
          >
            <header className="flex min-h-14 items-center justify-between border-b border-gray-200 px-4 dark:border-gray-700">
              <h2 id="visitor-drawer-title" className="font-semibold text-gray-900 dark:text-gray-100">
                {t('visitor.ui.panelTitle', '访客信息')}
              </h2>
              <button
                type="button"
                autoFocus
                onClick={() => setIsVisitorDrawerOpen(false)}
                aria-label={t('visitor.ui.closePanel', '关闭访客信息')}
                className="flex h-9 w-9 items-center justify-center rounded-md text-gray-600 outline-none hover:bg-gray-100 hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </header>
            <div className="min-h-0 flex-1">
              <VisitorPanel activeChat={activeChat} variant="drawer" />
            </div>
          </aside>
        </div>
      )}
    </div>
  );
};

export default ChatPage;
