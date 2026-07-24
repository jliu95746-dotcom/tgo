import React, { useCallback, useMemo, useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock3, Plus, Tags, X } from 'lucide-react';
import { createPortal } from 'react-dom';
import { MessagePayloadType, PlatformType, type ChannelInfo, type ChannelVisitorExtra, type Chat } from '@/types';
import { useChatStore, chatSelectors, useUIStore, useConversationStore } from '@/stores';
import { useSyncStore } from '@/stores/syncStore';
import { useChannelStore } from '@/stores/channelStore';
import { conversationsApi } from '@/services/conversationsApi';
import { tagsApiService, type TagResponse } from '@/services/tagsApi';
import { wukongimWebSocketService } from '@/services/wukongimWebSocket';
import { getChannelKey } from '@/utils/channelUtils';
import { ChatListHeader } from '@/components/chat/ChatListHeader';
import { ChatListEmpty } from '@/components/chat/ChatListEmpty';
import { ChatListItem } from '@/components/chat/ChatListItem';
import { UnassignedChatListItem } from '@/components/chat/UnassignedChatListItem';
import { OnlineVisitorListItem } from '@/components/chat/OnlineVisitorListItem';
import { ChatListTabs, ChatTabType } from '@/components/chat/ChatListTabs';
import OnboardingSidebarPanel from '@/components/onboarding/OnboardingSidebarPanel';
import { visitorApiService, type VisitorResponse } from '@/services/visitorApi';
import { CHAT_PRIORITY, CHAT_STATUS, VISITOR_STATUS } from '@/constants';
import { useToast } from '@/hooks/useToast';
import { showApiError } from '@/utils/toastHelpers';

// ============================================================================
// Main Component
// ============================================================================

/**
 * Props for the ChatList component
 */
interface ChatListProps {
  /** Currently active chat */
  activeChat?: Chat;
  /** Callback when a chat is selected */
  onChatSelect: (chat: Chat) => void;
  /** Active tab (controlled by parent if provided) */
  activeTab?: ChatTabType;
  /** Callback when tab changes */
  onTabChange?: (tab: ChatTabType) => void;
  /** Trigger to refresh lists (increment to trigger refresh) */
  refreshTrigger?: number;
  /** Channel info of the deleted chat (to remove from local state) */
  deletedChatChannel?: { channelId: string; channelType: number } | null;
  /** Optional responsive layout classes */
  className?: string;
  /** Reports the number of items visible in the current view */
  onVisibleItemCountChange?: (count: number) => void;
}

/**
 * Custom hook for managing chat list filtering with search
 */
const useSearchFiltering = (chats: Chat[], searchQuery: string) => {
  return useMemo(() => {
    if (!searchQuery.trim()) return chats;
    
    const lowerQuery = searchQuery.toLowerCase();
    return chats.filter((chat: Chat) => {
      const baseId = chat.channelId || chat.id;
      const name = (chat.channelInfo?.name || `访客${String(baseId).slice(-4)}`).toLowerCase();
      return name.includes(lowerQuery) || chat.lastMessage.toLowerCase().includes(lowerQuery);
    });
  }, [chats, searchQuery]);
};

/**
 * Sort chats by timestamp (desc)
 */
const sortChatsByTimestamp = (chats: Chat[]): Chat[] => {
  return [...chats].sort((a, b) => {
    const aSec = a.lastTimestampSec ?? (a.timestamp ? Math.floor(new Date(a.timestamp).getTime() / 1000) : 0);
    const bSec = b.lastTimestampSec ?? (b.timestamp ? Math.floor(new Date(b.timestamp).getTime() / 1000) : 0);
    return bSec - aSec;
  });
};

import { normalizeTagHex, hexToRgba } from '@/utils/tagUtils';

type StoredVisitorTag = {
  id: string;
  display_name?: string;
  name?: string;
  color?: string | null;
};

/**
 * Chat list sidebar component
 * Displays a list of conversations with search and sync functionality
 *
 * Features:
 * - Tab filtering (Mine, Unassigned, All) - each tab has its own data source
 * - "我的": /conversations/my + 新消息创建的会话
 * - "未分配": /conversations/waiting
 * - "全部": /conversations/all
 * - Search filtering by visitor name or last message
 * - Real-time sync with WuKongIM
 * - Empty state when no conversations exist
 * - Optimized rendering with memoized sub-components
 */
const ChatListComponent: React.FC<ChatListProps> = ({ 
  activeChat, 
  onChatSelect,
  activeTab: controlledActiveTab,
  onTabChange: controlledOnTabChange,
  refreshTrigger,
  deletedChatChannel,
  className = '',
  onVisibleItemCountChange,
}) => {
  const { t } = useTranslation();
  
  // Store subscriptions - chats 用于存储新消息创建的会话
  const realtimeChatsState = useChatStore(chatSelectors.chats);
  const realtimeChats = useMemo(() => realtimeChatsState ?? [], [realtimeChatsState]);
  const searchQuery = useChatStore(chatSelectors.searchQuery) ?? '';
  const setSearchQuery = useChatStore(state => state.setSearchQuery);
  
  // Get convertWuKongIMToChat from syncStore
  const convertWuKongIMToChat = useSyncStore(state => state.convertWuKongIMToChat);
  
  // Get seedChannel from channelStore to cache channel info from API responses
  const seedChannel = useChannelStore(state => state.seedChannel);

  // "我的" tab 标签筛选
  const mineTagIds = useConversationStore(state => state.mineTagIds);
  const setMineTagIds = useConversationStore(state => state.setMineTagIds);
  const [mineTagMeta, setMineTagMeta] = useState<Record<string, StoredVisitorTag>>({});
  const [availableVisitorTags, setAvailableVisitorTags] = useState<TagResponse[]>([]);
  const [isLoadingTags, setIsLoadingTags] = useState(false);
  const [isTagFilterOpen, setIsTagFilterOpen] = useState(false);
  const tagFilterRef = useRef<HTMLDivElement>(null);
  const tagPickerRef = useRef<HTMLDivElement>(null);
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const [tagPickerPos, setTagPickerPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const [tagSearch, setTagSearch] = useState('');

  // 标记是否已完成初始化
  const [mineTagInitialized] = useState(true);
  
  // 当标签筛选开启但频道信息（含 tags）异步回填失败时：做有限重试，避免会话误入筛选结果或无限请求
  const [channelInfoRetryTick, setChannelInfoRetryTick] = useState(0);
  const channelInfoRetryCountRef = useRef<Record<string, number>>({});
  const channelInfoPendingRef = useRef<Set<string>>(new Set());
  // 标签筛选开启时，如果某会话的频道信息最终获取失败：允许“兜底显示”在列表里，避免造成“会话/消息丢了”的假象
  // 一旦后续成功拿到 tags，会自动移除兜底标记并回归正常筛选逻辑
  const [tagFilterBypassKeys, setTagFilterBypassKeys] = useState<Record<string, true>>({});

  // Connection status from uiStore
  const isConnected = useUIStore(state => state.isConnected);
  const isConnecting = useUIStore(state => state.isConnecting);

  // Local state for tabs (used when not controlled by parent)
  const [internalActiveTab, setInternalActiveTab] = useState<ChatTabType>('mine');
  
  // Use controlled tab if provided, otherwise use internal state
  const activeTab = controlledActiveTab ?? internalActiveTab;
  const setActiveTab = controlledOnTabChange ?? setInternalActiveTab;
  
  // 每个 tab 独立的会话列表
  const [myChats, setMyChats] = useState<Chat[]>([]);
  const [unassignedChats, setUnassignedChats] = useState<Chat[]>([]);
  const [allChats, setAllChats] = useState<Chat[]>([]);
  const [manualChats, setManualChats] = useState<Chat[]>([]);
  const [recentVisitors, setRecentVisitors] = useState<VisitorResponse[]>([]);
  
  // Loading state for each tab
  const [isLoadingMine, setIsLoadingMine] = useState(false);
  const [isLoadingUnassigned, setIsLoadingUnassigned] = useState(false);
  const [isLoadingAll, setIsLoadingAll] = useState(false);
  const [isLoadingManual, setIsLoadingManual] = useState(false);
  const [isLoadingRecent, setIsLoadingRecent] = useState(false);
  
  // Loading more state for pagination
  const [isLoadingMoreUnassigned, setIsLoadingMoreUnassigned] = useState(false);
  const [isLoadingMoreAll, setIsLoadingMoreAll] = useState(false);
  const [isLoadingMoreManual, setIsLoadingMoreManual] = useState(false);
  const [isLoadingMoreRecent, setIsLoadingMoreRecent] = useState(false);
  
  // Has more data for pagination
  const [hasMoreUnassigned, setHasMoreUnassigned] = useState(false);
  const [hasMoreAll, setHasMoreAll] = useState(false);
  const [hasMoreManual, setHasMoreManual] = useState(false);
  const [hasMoreRecent, setHasMoreRecent] = useState(false);
  
  // Track which tabs have been loaded (to prevent duplicate requests on mount)
  const loadedTabsRef = useRef<Set<ChatTabType>>(new Set());
  
  // Scroll container ref for infinite scroll
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  
  // 未分配数量（从 API 获取，监听 queue.updated 事件更新）
  const [unassignedCount, setUnassignedCount] = useState<number>(0);
  
  // 获取未分配数量的函数
  const fetchUnassignedCount = useCallback(async () => {
    try {
      const response = await conversationsApi.getWaitingQueueCount();
      // API 可能返回 { count: number } 或 { waiting: number }
      const count = response.waiting ?? 0;
      setUnassignedCount(count);
      console.log('📋 ChatList: Fetched unassigned count:', count, response);
    } catch (error) {
      console.error('📋 ChatList: Failed to fetch unassigned count:', error);
    }
  }, []);
  
  // 初始化时获取一次，并监听 queue.updated 事件
  useEffect(() => {
    // 立即获取一次
    fetchUnassignedCount();
    
    // 监听 queue.updated 事件
    const unsubscribe = wukongimWebSocketService.onQueueUpdated(() => {
      console.log('📋 ChatList: queue.updated event received, refreshing count');
      fetchUnassignedCount();
    });
    
    // 清理订阅
    return () => unsubscribe();
  }, [fetchUnassignedCount]);
  
  // 获取"我的"会话
  const fetchMyConversations = useCallback(async (force = false) => {
    if (!force && loadedTabsRef.current.has('mine')) return;
    loadedTabsRef.current.add('mine');
    
    setIsLoadingMine(true);
    try {
      const response = await conversationsApi.getMyConversations(1, {
        tag_ids: mineTagIds.length > 0 ? mineTagIds : undefined,
      });
      if (response?.conversations) {
        const chats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setMyChats(sortChatsByTimestamp(chats));
        console.log(`📋 ChatList: Loaded "mine" tab, ${chats.length} conversations`);
        
        // 缓存频道信息，避免后续单独请求
        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
          console.log(`📋 ChatList: Cached ${response.channels.length} channels from "mine" tab`);
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load "mine" conversations:', error);
    } finally {
      setIsLoadingMine(false);
    }
  }, [convertWuKongIMToChat, seedChannel, mineTagIds]);

  // 加载可选访客标签（用于“我的”标签筛选）
  useEffect(() => {
    let mounted = true;
    if (!isTagFilterOpen) return; // 只在面板打开时请求
    (async () => {
      setIsLoadingTags(true);
      try {
        const resp = await tagsApiService.listVisitorTags({ limit: 100, offset: 0 });
        if (!mounted) return;
        setAvailableVisitorTags(resp.data ?? []);

        // 补齐已选标签的展示信息（避免分页导致找不到显示名/颜色）
        if (resp.data && resp.data.length > 0) {
          setMineTagMeta((prev) => {
            const next = { ...prev };
            mineTagIds.forEach((id: string) => {
              const found = resp.data!.find((t) => t.id === id);
              if (found) {
                next[id] = {
                  id: found.id,
                  display_name: found.display_name,
                  name: found.name,
                  color: found.color ?? null,
                };
              }
            });
            return next;
          });
        }
      } catch {
        // 静默失败：不影响会话列表使用
        if (!mounted) return;
        setAvailableVisitorTags([]);
      } finally {
        if (mounted) setIsLoadingTags(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [isTagFilterOpen, mineTagIds]);

  // 点击外部关闭标签筛选面板
  useEffect(() => {
    if (!isTagFilterOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      const el = tagFilterRef.current;
      const pickerEl = tagPickerRef.current;
      if (e.target && el && el.contains(e.target as Node)) return;
      if (e.target && pickerEl && pickerEl.contains(e.target as Node)) return;
      setIsTagFilterOpen(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [isTagFilterOpen]);

  // 打开标签选择面板时计算锚点位置（Portal + fixed，避免被列表遮挡/裁剪）
  useEffect(() => {
    if (!isTagFilterOpen) return;

    const updatePos = () => {
      const btn = addButtonRef.current;
      if (!btn) return;
      const rect = btn.getBoundingClientRect();
      const width = 260;
      const left = Math.max(50, Math.min(window.innerWidth - width - 8, rect.right - width)); // 右对齐按钮
      const top = Math.min(window.innerHeight - 8, rect.bottom + 8);
      setTagPickerPos({ top, left, width });
    };

    updatePos();
    window.addEventListener('resize', updatePos);
    // 捕获所有滚动（含列表滚动），确保位置跟随
    window.addEventListener('scroll', updatePos, true);
    return () => {
      window.removeEventListener('resize', updatePos);
      window.removeEventListener('scroll', updatePos, true);
    };
  }, [isTagFilterOpen]);

  // 标签筛选变化时，强制刷新"我的"会话（避免 loadedTabsRef 阻止刷新）
  // 必须等 localStorage 初始化完成后才触发，避免空数组时的无效请求
  useEffect(() => {
    if (!mineTagInitialized) return; // 等待初始化完成
    if (activeTab === 'mine') {
      loadedTabsRef.current.delete('mine');
      fetchMyConversations(true);
    }
  }, [mineTagInitialized, mineTagIds, activeTab, fetchMyConversations]);
  
  // 每页会话数量
  const PAGE_SIZE = 20;
  
  // 获取"未分配"会话（首次加载）
  const fetchUnassignedConversations = useCallback(async () => {
    setIsLoadingUnassigned(true);
    try {
      const response = await conversationsApi.getWaitingConversations(20, PAGE_SIZE, 0);
      if (response?.conversations) {
        const chats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setUnassignedChats(sortChatsByTimestamp(chats));
        setHasMoreUnassigned(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded "unassigned" tab, ${chats.length} conversations, hasMore: ${response.pagination?.has_next}`);
        
        // 缓存频道信息，避免后续单独请求
        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
          console.log(`📋 ChatList: Cached ${response.channels.length} channels from "unassigned" tab`);
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load "unassigned" conversations:', error);
    } finally {
      setIsLoadingUnassigned(false);
    }
  }, [convertWuKongIMToChat, seedChannel]);
  
  // 加载更多"未分配"会话
  const loadMoreUnassignedConversations = useCallback(async () => {
    if (isLoadingMoreUnassigned || !hasMoreUnassigned) return;
    
    setIsLoadingMoreUnassigned(true);
    try {
      const offset = unassignedChats.length;
      const response = await conversationsApi.getWaitingConversations(20, PAGE_SIZE, offset);
      if (response?.conversations) {
        const newChats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setUnassignedChats(prev => [...prev, ...newChats]);
        setHasMoreUnassigned(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded more "unassigned", +${newChats.length} conversations, hasMore: ${response.pagination?.has_next}`);
        
        // 缓存频道信息
        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load more "unassigned" conversations:', error);
    } finally {
      setIsLoadingMoreUnassigned(false);
    }
  }, [isLoadingMoreUnassigned, hasMoreUnassigned, unassignedChats.length, convertWuKongIMToChat, seedChannel]);
  
  // 获取"已完成"会话（每次切换到此 tab 都调用）
  const fetchAllConversations = useCallback(async () => {
    setIsLoadingAll(true);
    try {
      const response = await conversationsApi.getAllConversations(20, PAGE_SIZE, 0, { only_completed_recent: true });
      if (response?.conversations) {
        const chats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setAllChats(sortChatsByTimestamp(chats));
        setHasMoreAll(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded "all" tab, ${chats.length} conversations, hasMore: ${response.pagination?.has_next}`);
        
        // 缓存频道信息，避免后续单独请求
        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
          console.log(`📋 ChatList: Cached ${response.channels.length} channels from "all" tab`);
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load "all" conversations:', error);
    } finally {
      setIsLoadingAll(false);
    }
  }, [convertWuKongIMToChat, seedChannel]);
  
  // 加载更多"已完成"会话
  const loadMoreAllConversations = useCallback(async () => {
    if (isLoadingMoreAll || !hasMoreAll) return;
    
    setIsLoadingMoreAll(true);
    try {
      const offset = allChats.length;
      const response = await conversationsApi.getAllConversations(20, PAGE_SIZE, offset, { only_completed_recent: true });
      if (response?.conversations) {
        const newChats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setAllChats(prev => [...prev, ...newChats]);
        setHasMoreAll(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded more "all", +${newChats.length} conversations, hasMore: ${response.pagination?.has_next}`);
        
        // 缓存频道信息
        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load more "all" conversations:', error);
    } finally {
      setIsLoadingMoreAll(false);
    }
  }, [isLoadingMoreAll, hasMoreAll, allChats.length, convertWuKongIMToChat, seedChannel]);

  // 获取"转人工"会话（按访客标签筛选，manual_service_contain=true）
  const fetchManualConversations = useCallback(async () => {
    setIsLoadingManual(true);
    try {
      const response = await conversationsApi.getRecentConversationsByTagsRecent({
        manual_service_contain: true,
        msg_count: 20,
        limit: PAGE_SIZE,
        offset: 0,
      });
      if (response?.conversations) {
        const chats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setManualChats(sortChatsByTimestamp(chats));
        setHasMoreManual(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded "manual" tab, ${chats.length} conversations, hasMore: ${response.pagination?.has_next}`);

        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
          console.log(`📋 ChatList: Cached ${response.channels.length} channels from "manual" tab`);
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load "manual" conversations:', error);
    } finally {
      setIsLoadingManual(false);
    }
  }, [convertWuKongIMToChat, seedChannel]);

  const loadMoreManualConversations = useCallback(async () => {
    if (isLoadingMoreManual || !hasMoreManual) return;
    setIsLoadingMoreManual(true);
    try {
      const offset = manualChats.length;
      const response = await conversationsApi.getRecentConversationsByTagsRecent({
        manual_service_contain: true,
        msg_count: 20,
        limit: PAGE_SIZE,
        offset,
      });
      if (response?.conversations) {
        const newChats = response.conversations.map(conv => convertWuKongIMToChat(conv));
        setManualChats(prev => [...prev, ...newChats]);
        setHasMoreManual(response.pagination?.has_next ?? false);

        if (response.channels && response.channels.length > 0) {
          response.channels.forEach(channel => {
            if (channel.channel_id && channel.channel_type != null) {
              seedChannel(channel.channel_id, channel.channel_type, channel);
            }
          });
        }
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load more "manual" conversations:', error);
    } finally {
      setIsLoadingMoreManual(false);
    }
  }, [isLoadingMoreManual, hasMoreManual, manualChats.length, convertWuKongIMToChat, seedChannel]);

  // 获取"最近在线"访客
  const fetchRecentVisitors = useCallback(async () => {
    setIsLoadingRecent(true);
    try {
      const response = await visitorApiService.listVisitors({
        service_status: ['new'],
        sort_by: 'last_offline_time',
        sort_order: 'desc',
        limit: PAGE_SIZE,
        offset: 0,
      });
      if (response?.data) {
        setRecentVisitors(response.data);
        setHasMoreRecent(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded "recent" tab, ${response.data.length} visitors, hasMore: ${response.pagination?.has_next}`);
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load "recent" visitors:', error);
    } finally {
      setIsLoadingRecent(false);
    }
  }, []);

  const loadMoreRecentVisitors = useCallback(async () => {
    if (isLoadingMoreRecent || !hasMoreRecent) return;
    setIsLoadingMoreRecent(true);
    try {
      const offset = recentVisitors.length;
      const response = await visitorApiService.listVisitors({
        service_status: ['new'],
        sort_by: 'last_offline_time',
        sort_order: 'desc',
        limit: PAGE_SIZE,
        offset,
      });
      if (response?.data) {
        setRecentVisitors(prev => [...prev, ...response.data]);
        setHasMoreRecent(response.pagination?.has_next ?? false);
        console.log(`📋 ChatList: Loaded more "recent", +${response.data.length} visitors, hasMore: ${response.pagination?.has_next}`);
      }
    } catch (error) {
      console.error('📋 ChatList: Failed to load more "recent" visitors:', error);
    } finally {
      setIsLoadingMoreRecent(false);
    }
  }, [isLoadingMoreRecent, hasMoreRecent, recentVisitors.length]);
  
  // 根据当前 tab 获取对应数据（组件挂载时和 tab 切换时）
  // 注意：'mine' tab 的请求由标签筛选 effect 统一处理，这里不再单独调用
  useEffect(() => {
    if (activeTab === 'unassigned') {
      fetchUnassignedConversations();
    } else if (activeTab === 'all') {
      fetchAllConversations();
    } else if (activeTab === 'manual') {
      fetchManualConversations();
    } else if (activeTab === 'recent') {
      fetchRecentVisitors();
    }
    // 'mine' tab 不在这里处理，由标签筛选 effect 统一管理
  }, [activeTab, fetchUnassignedConversations, fetchAllConversations, fetchManualConversations, fetchRecentVisitors]);
  
  // 当 refreshTrigger 变化时，强制刷新"我的"和"未分配"列表及数量
  const prevRefreshTriggerRef = useRef(refreshTrigger);
  useEffect(() => {
    // 只在 refreshTrigger 变化时触发（而不是初次挂载）
    if (refreshTrigger !== undefined && refreshTrigger !== prevRefreshTriggerRef.current) {
      prevRefreshTriggerRef.current = refreshTrigger;
      console.log('📋 ChatList: refreshTrigger changed, refreshing lists');
      // 强制刷新"我的"会话
      loadedTabsRef.current.delete('mine');
      fetchMyConversations(true);
      // 刷新未分配列表和数量
      fetchUnassignedConversations();
      fetchUnassignedCount();
    }
  }, [refreshTrigger, fetchMyConversations, fetchUnassignedConversations, fetchUnassignedCount]);
  
  // 追踪上一次处理的 deletedChatChannel，避免重复处理
  const lastDeletedChannelRef = useRef<string | null>(null);
  
  // 当 deletedChatChannel 变化时，从本地状态中移除该会话并选中下一个
  useEffect(() => {
    if (deletedChatChannel?.channelId && deletedChatChannel?.channelType != null) {
      const { channelId, channelType } = deletedChatChannel;
      const key = getChannelKey(channelId, channelType);
      
      // 避免重复处理同一个删除
      if (lastDeletedChannelRef.current === key) {
        return;
      }
      lastDeletedChannelRef.current = key;
      
      console.log('📋 ChatList: Removing deleted chat from local state:', key);
      
      // 从本地状态中移除（使用函数式更新，不依赖外部状态）
      setMyChats(prev => {
        const remaining = prev.filter(c => !(c.channelId === channelId && c.channelType === channelType));
        
        // 如果被删除的是当前选中的会话，选中下一个
        if (activeChat?.channelId === channelId && activeChat?.channelType === channelType && remaining.length > 0 && activeTab === 'mine') {
          const deletedIndex = prev.findIndex(c => c.channelId === channelId && c.channelType === channelType);
          const nextIndex = Math.min(deletedIndex, remaining.length - 1);
          const nextChat = remaining[Math.max(0, nextIndex)];
          console.log('📋 ChatList: Selecting next chat:', nextChat.channelId);
          // 使用 setTimeout 避免在 setState 回调中调用
          setTimeout(() => onChatSelect(nextChat), 0);
        }
        
        return remaining;
      });
      setAllChats(prev => prev.filter(c => !(c.channelId === channelId && c.channelType === channelType)));
    }
  }, [deletedChatChannel, activeChat, activeTab, onChatSelect]);
  
  // 合并"我的"会话：API 返回的 + 新消息创建的会话
  // 优先使用 realtimeChats 中的更新数据（包含最新的 lastMessage 和 unreadCount）
  // 但只在 realtimeChats 的数据比 API 的更新时才使用
  const mergedMyChats = useMemo(() => {
    const selectedTagIds = (mineTagIds ?? []).filter(Boolean);
    const selectedTagSet = new Set(selectedTagIds);
    const hasTagFilter = selectedTagSet.size > 0;

    const shouldBypassFilter = (chat: Chat): boolean => {
      if (!hasTagFilter) return false;
      if (!chat.channelId || chat.channelType == null) return false;
      const key = getChannelKey(chat.channelId, chat.channelType);
      return tagFilterBypassKeys[key] === true;
    };

    const extractChannelTagIds = (chat: Chat): string[] => {
      const extra: any = (chat.channelInfo as any)?.extra;
      const tags: any[] = Array.isArray(extra?.tags) ? extra.tags : [];
      const ids = tags
        .map((t) => (t?.id ?? t?.tag_id ?? t?.tagId ?? t?.value ?? null))
        .filter((x) => typeof x === 'string' && x.trim().length > 0)
        .map((x) => String(x));
      return ids;
    };

    const matchTagFilter = (chat: Chat): boolean => {
      if (!hasTagFilter) return true;
      // 若该会话已被标记为“频道信息获取失败”，兜底展示（避免造成会话/消息丢失假象）
      if (shouldBypassFilter(chat)) return true;
      // 标签来自频道信息（异步）；未拿到标签前先不展示，避免把不匹配的访客混入筛选结果
      const ids = extractChannelTagIds(chat);
      if (ids.length === 0) return false;
      return ids.some((id) => selectedTagSet.has(id));
    };

    // 建立 realtimeChats 的 key -> chat 映射，用于快速查找
    const realtimeChatMap = new Map<string, Chat>();
    realtimeChats.forEach(c => {
      const key = getChannelKey(c.channelId, c.channelType);
      realtimeChatMap.set(key, c);
    });
    
    // 合并 API 会话，如果 realtimeChats 中有更新（时间戳更晚或内容不同）则使用更新后的数据
    const mergedFromApi = myChats.map(apiChat => {
      const key = getChannelKey(apiChat.channelId, apiChat.channelType);
      const realtimeChat = realtimeChatMap.get(key);
      if (realtimeChat) {
        const apiTimestamp = apiChat.lastTimestampSec ?? 0;
        const realtimeTimestamp = realtimeChat.lastTimestampSec ?? 0;
        
        // 判定实时数据是否更新：
        // 1. 时间戳更晚
        // 2. 时间戳相同但内容不同（例如流式消息增量更新预览）
        const isNewer = realtimeTimestamp > apiTimestamp;
        const isContentUpdated = realtimeTimestamp === apiTimestamp && realtimeChat.lastMessage !== apiChat.lastMessage;

        if ((isNewer || isContentUpdated) && (realtimeChat.lastMessage || realtimeChat.payloadType === MessagePayloadType.STREAM)) {
          return {
            ...apiChat,
            lastMessage: realtimeChat.lastMessage,
            payloadType: realtimeChat.payloadType,
            timestamp: realtimeChat.timestamp,
            lastTimestampSec: realtimeChat.lastTimestampSec,
            unreadCount: realtimeChat.unreadCount,
            priority: realtimeChat.priority,
          };
        }
      }
      return apiChat;
    });
    
    // 获取 API 返回的会话 keys
    const apiChatKeys = new Set(myChats.map(c => getChannelKey(c.channelId, c.channelType)));
    
    // 过滤出不在 API 结果中的实时会话（新消息创建的，且有实际内容或特殊消息类型）
    const newRealtimeChats = realtimeChats.filter(
      c =>
        !apiChatKeys.has(getChannelKey(c.channelId, c.channelType)) &&
        (c.lastMessage || c.payloadType === MessagePayloadType.STREAM) &&
        matchTagFilter(c)
    );
    
    // 合并并排序
    return sortChatsByTimestamp([...mergedFromApi, ...newRealtimeChats]);
  }, [myChats, realtimeChats, mineTagIds, tagFilterBypassKeys]);

  // 当开启标签筛选时，实时新会话若缺少 channelInfo.extra.tags，会被隐藏；
  // 这里主动触发“刷新频道信息”来尽快拿到 tags（有限重试，避免无限请求）。
  useEffect(() => {
    if (!mineTagInitialized) return;
    if (activeTab !== 'mine') return;
    if (!mineTagIds || mineTagIds.length === 0) return;

    const apiChatKeys = new Set(myChats.map((c) => getChannelKey(c.channelId, c.channelType)));

    const getChannelTagState = (channelId: string, channelType: number) => {
      const channelStore = useChannelStore.getState();
      const info = channelStore.getChannel(channelId, channelType);
      const key = getChannelKey(channelId, channelType);
      const err = (channelStore as any).errors?.[key] as string | null | undefined;
      const extra: any = info?.extra;
      const hasTagsField = Array.isArray(extra?.tags);
      const tagIds: string[] = hasTagsField
        ? (extra.tags as any[])
            .map((t) => (t?.id ?? t?.tag_id ?? t?.tagId ?? t?.value ?? null))
            .filter((x) => typeof x === 'string' && x.trim().length > 0)
            .map((x) => String(x))
        : [];
      return { key, err, hasTagsField, tagIds };
    };

    const candidates = realtimeChats.filter((c) => {
      if (!c.lastMessage && c.payloadType !== MessagePayloadType.STREAM) return false;
      if (!c.channelId || c.channelType == null) return false;
      const key = getChannelKey(c.channelId, c.channelType);
      if (apiChatKeys.has(key)) return false;
      // 已被标记兜底展示的不再重试
      if (tagFilterBypassKeys[key] === true) return false;

      // 只有在“频道信息缺失/未包含 tags 字段”或“上次请求报错”时才触发刷新
      const state = getChannelTagState(c.channelId, c.channelType);
      if (state.tagIds.length > 0) return false; // 已有 tags，不需要
      if (state.err) return true; // 有错误，重试
      if (!state.hasTagsField) return true; // 还没加载到 tags 字段，重试
      // hasTagsField=true 且 tagIds 为空：代表成功拿到信息但就是没有标签，不应重试
      return false;
    });

    if (candidates.length === 0) return;

    // 限制并发：每轮最多触发 6 个刷新
    const toRefresh = candidates.slice(0, 6);
    toRefresh.forEach((c) => {
      const key = getChannelKey(c.channelId, c.channelType);
      if (channelInfoPendingRef.current.has(key)) return;
      const retries = channelInfoRetryCountRef.current[key] ?? 0;
      if (retries >= 3) return; // 最多重试 3 次

      channelInfoPendingRef.current.add(key);
      (async () => {
        try {
          // refreshChannel + apply 到 chats/messages（chatStore 已提供统一入口）
          await useChatStore.getState().syncChannelInfoAcrossUI(c.channelId!, c.channelType!);
        } catch {
          // ignore
        } finally {
          channelInfoPendingRef.current.delete(key);

          const latest = getChannelTagState(c.channelId!, c.channelType!);
          const hasTagsNow = latest.tagIds.length > 0;
          const hadError = !!latest.err;

          // 若成功拿到 tags：清除兜底标记、清除重试计数
          if (hasTagsNow) {
            channelInfoRetryCountRef.current[key] = 0;
            setTagFilterBypassKeys((prev) => {
              if (!prev[key]) return prev;
              const next = { ...prev };
              delete next[key];
              return next;
            });
            return;
          }

          // 若成功拿到信息且明确 tags 字段为空（无标签）：不兜底显示，也不重试
          if (!hadError && latest.hasTagsField) {
            channelInfoRetryCountRef.current[key] = 0;
            return;
          }

          // 请求失败或仍未拿到 tags 字段：进行退避重试；超过上限则“兜底显示”
          const nextRetry = (channelInfoRetryCountRef.current[key] ?? 0) + 1;
          channelInfoRetryCountRef.current[key] = nextRetry;
          if (nextRetry >= 3) {
            // 最终失败：兜底展示，避免造成会话/消息丢失假象
            setTagFilterBypassKeys((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
            return;
          }
          const delay = Math.min(8000, 800 * Math.pow(2, nextRetry - 1)); // 800ms, 1600ms
          setTimeout(() => setChannelInfoRetryTick((t) => t + 1), delay);
        }
      })();
    });
  }, [mineTagInitialized, activeTab, mineTagIds, myChats, realtimeChats, channelInfoRetryTick, tagFilterBypassKeys]);

  // "已完成"会话：仅使用 API 返回结果（不合并实时会话，避免把活跃会话混入“已完成”）
  const mergedAllChats = useMemo(() => {
    return sortChatsByTimestamp(allChats);
  }, [allChats]);

  // Get the appropriate chat list based on active tab
  const getChatsForTab = useCallback((): Chat[] => {
    switch (activeTab) {
      case 'mine':
        return mergedMyChats;
      case 'unassigned':
        return unassignedChats;
      case 'all':
        return mergedAllChats;
      case 'manual':
        return manualChats;
      default:
        return mergedMyChats;
    }
  }, [activeTab, mergedMyChats, unassignedChats, mergedAllChats, manualChats]);

  // Calculate counts for tabs
  // "我的" tab 显示会话数量，"未分配" tab 显示等待数量
  const counts = useMemo(() => {
    return {
      mine: mergedMyChats.length,
      unassigned: unassignedCount,
    };
  }, [mergedMyChats.length, unassignedCount]);

  // Get chats for current tab
  const tabChats = getChatsForTab();
  
  // Apply search filtering
  const filteredChats = useSearchFiltering(tabChats, searchQuery);

  const filteredRecentVisitors = useMemo(() => {
    if (!searchQuery.trim()) return recentVisitors;
    const lowerQuery = searchQuery.toLowerCase();
    return recentVisitors.filter(v => {
      const name = (v.name || v.display_nickname || v.nickname_zh || v.nickname || `访客${String(v.id).slice(-4)}`).toLowerCase();
      return name.includes(lowerQuery);
    });
  }, [recentVisitors, searchQuery]);

  const visibleItemCount = activeTab === 'recent'
    ? filteredRecentVisitors.length
    : filteredChats.length;

  useEffect(() => {
    onVisibleItemCountChange?.(visibleItemCount);
  }, [onVisibleItemCountChange, visibleItemCount]);

  // Memoized callbacks to prevent unnecessary re-renders
  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
  }, [setSearchQuery]);

  const handleTabChange = useCallback((tab: ChatTabType) => {
    setIsTagFilterOpen(false);
    setActiveTab(tab);
  }, [setActiveTab]);

  // Handle chat click - clear unread count locally and call parent handler
  const handleChatClick = useCallback((chat: Chat) => {
    // Don't clear unread for unassigned tab
    if (activeTab !== 'unassigned' && (chat.unreadCount || 0) > 0) {
      const updateChatUnread = (chats: Chat[]) => 
        chats.map(c => 
          c.channelId === chat.channelId && c.channelType === chat.channelType
            ? { ...c, unreadCount: 0 }
            : c
        );
      
      // Update local state for the appropriate tab
      setMyChats(updateChatUnread);
      setAllChats(updateChatUnread);
    }
    
    // Call parent handler
    onChatSelect(chat);
  }, [activeTab, onChatSelect]);

  const { showToast } = useToast();

  const handleOnlineVisitorClick = useCallback((v: VisitorResponse) => {
    try {
      const visitorId = v?.id;
      if (!visitorId) {
        showToast('warning', t('search.toasts.incompleteVisitorInfo', '访客信息不完整'), t('search.toasts.missingVisitorId', '缺少 visitor_id'));
        return;
      }
      const channelId = `${visitorId}-vtr`;
      const channelType = 251; // 访客会话类型

      // 1) 已存在则直接切换
      const exist = realtimeChats.find(c => c.channelId === channelId && c.channelType === channelType);
      if (exist) {
        onChatSelect(exist);
        return;
      }

      // 2) 不存在则新建 Chat 对象并插入到顶部
      const fallbackName = t('visitor.fallbackName', '访客 {{suffix}}').replace('{{suffix}}', (v.platform_open_id || '').slice(-4));
      const rawName = (v.name || v.display_nickname || v.nickname_zh || v.nickname || fallbackName) as string;
      const plainName = (rawName || '').replace(/<[^>]*>/g, '') || fallbackName;

      const key = getChannelKey(channelId, channelType);
      const nowIso = new Date().toISOString();
      const nowSec = Math.floor(Date.now() / 1000);

      const extra: ChannelVisitorExtra = {
        id: v.id,
        platform_id: v.platform_id,
        platform_type: (v.platform_type as PlatformType ?? PlatformType.WEBSITE),
        platform_open_id: v.platform_open_id,
        name: v.name || undefined,
        nickname: v.nickname || undefined,
        display_nickname: v.display_nickname || undefined,
        avatar_url: v.avatar_url || undefined,
        is_online: v.is_online,
        created_at: v.created_at,
        updated_at: v.updated_at,
        ai_disabled: v.ai_disabled ?? undefined,
        ai_settings: (v as any).ai_settings,
        tags: v.tags as any,
      };

      const channelInfo: ChannelInfo = {
        name: plainName,
        avatar: v.avatar_url || '',
        channel_id: channelId,
        channel_type: channelType,
        extra,
      };

      const newChat: Chat = {
        id: key,
        platform: (v.platform_type ?? PlatformType.WEBSITE) as unknown as string,
        lastMessage: '',
        timestamp: nowIso,
        lastTimestampSec: nowSec,
        status: CHAT_STATUS.ACTIVE,
        unreadCount: 0,
        channelId,
        channelType,
        lastMsgSeq: 0,
        channelInfo,
        tags: (v.tags || []).map(t => ({ name: t.name, color: t.color })),
        priority: CHAT_PRIORITY.NORMAL,
        visitorStatus: v.is_online ? VISITOR_STATUS.ONLINE : VISITOR_STATUS.OFFLINE,
      } as any;

      useChatStore.getState().setChats([newChat, ...realtimeChats]);
      onChatSelect(newChat);
    } catch (e) {
      console.error('打开访客会话失败', e);
      showApiError(showToast, e);
    }
  }, [t, onChatSelect, realtimeChats, showToast]);

  // Loading state based on active tab
  const isLoading = useMemo(() => {
    switch (activeTab) {
      case 'mine':
        return isLoadingMine;
      case 'unassigned':
        return isLoadingUnassigned;
      case 'all':
        return isLoadingAll;
      case 'manual':
        return isLoadingManual;
      case 'recent':
        return isLoadingRecent;
      default:
        return false;
    }
  }, [activeTab, isLoadingMine, isLoadingUnassigned, isLoadingAll, isLoadingManual, isLoadingRecent]);
  
  // 是否正在加载更多
  const isLoadingMore = useMemo(() => {
    switch (activeTab) {
      case 'unassigned':
        return isLoadingMoreUnassigned;
      case 'all':
        return isLoadingMoreAll;
      case 'manual':
        return isLoadingMoreManual;
      case 'recent':
        return isLoadingMoreRecent;
      default:
        return false;
    }
  }, [activeTab, isLoadingMoreUnassigned, isLoadingMoreAll, isLoadingMoreManual, isLoadingMoreRecent]);
  
  // 是否还有更多数据
  const hasMore = useMemo(() => {
    switch (activeTab) {
      case 'unassigned':
        return hasMoreUnassigned;
      case 'all':
        return hasMoreAll;
      case 'manual':
        return hasMoreManual;
      case 'recent':
        return hasMoreRecent;
      default:
        return false;
    }
  }, [activeTab, hasMoreUnassigned, hasMoreAll, hasMoreManual, hasMoreRecent]);
  
  // 滚动事件处理 - 上拉加载更多
  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const target = e.currentTarget;
    const scrollTop = target.scrollTop;
    const scrollHeight = target.scrollHeight;
    const clientHeight = target.clientHeight;
    
    // 当滚动到距离底部 100px 时触发加载更多
    const threshold = 100;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < threshold;
    
    if (isNearBottom && !isLoadingMore && hasMore) {
      if (activeTab === 'unassigned') {
        loadMoreUnassignedConversations();
      } else if (activeTab === 'all') {
        loadMoreAllConversations();
      } else if (activeTab === 'manual') {
        loadMoreManualConversations();
      } else if (activeTab === 'recent') {
        loadMoreRecentVisitors();
      }
    }
  }, [activeTab, isLoadingMore, hasMore, loadMoreUnassignedConversations, loadMoreAllConversations, loadMoreManualConversations, loadMoreRecentVisitors]);

  return (
    <div className={`h-full w-[280px] shrink-0 bg-white/90 dark:bg-gray-800/90 backdrop-blur-lg border-r border-gray-200/60 dark:border-gray-700/60 flex flex-col ${className}`}>
      {/* Header with search */}
      <ChatListHeader
        searchQuery={searchQuery}
        onSearchChange={handleSearchChange}
      />

      {/* Connection Status Banner */}
      {!isConnected && (
        <div className={`px-4 py-2 flex items-center gap-2 ${isConnecting ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400' : 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400'} border-b border-gray-200/60 dark:border-gray-700/60`}>
          <div className="w-4 h-4 flex-shrink-0 flex items-center justify-center">
            {isConnecting ? (
              <div className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
            ) : (
              <X size={14} />
            )}
          </div>
          <span className="text-xs font-medium flex-grow">
            {isConnecting ? t('chat.status.connecting', '正在连接服务器...') : t('chat.status.disconnected', '网络已断开')}
          </span>
          {!isConnecting && (
            <button 
              onClick={() => window.location.reload()}
              className="text-[10px] underline hover:no-underline"
            >
              {t('common.retry', '重试')}
            </button>
          )}
        </div>
      )}

      {/* Tabs */}
      <ChatListTabs 
        activeTab={activeTab} 
        onTabChange={handleTabChange} 
        counts={counts}
      />

      {/* 访客筛选与“我的”标签筛选 */}
      <div className="px-3 py-2 border-b border-gray-200/60 dark:border-gray-700/60 bg-white/90 dark:bg-gray-800/90 backdrop-blur-lg">
        <div ref={tagFilterRef} className="relative z-40">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-pressed={activeTab === 'recent'}
              onClick={() => handleTabChange(activeTab === 'recent' ? 'mine' : 'recent')}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 ${
                activeTab === 'recent'
                  ? 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-300'
                  : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700'
              }`}
            >
              <Clock3 className="h-4 w-4" aria-hidden="true" />
              <span>{t('chat.list.tabs.recent', '最近在线')}</span>
            </button>

            {activeTab === 'mine' && (
              <button
                ref={addButtonRef}
                type="button"
                aria-expanded={isTagFilterOpen}
                aria-haspopup="dialog"
                onClick={() => setIsTagFilterOpen(value => !value)}
                className={`inline-flex min-h-9 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 ${
                  isTagFilterOpen || mineTagIds.length > 0
                    ? 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-300'
                    : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700'
                }`}
              >
                <Tags className="h-4 w-4" aria-hidden="true" />
                <span>{t('chat.list.tagFilter.button', '标签筛选')}</span>
                {mineTagIds.length > 0 && (
                  <span className="min-w-5 rounded-full bg-blue-100 px-1.5 py-0.5 text-center text-[10px] tabular-nums text-blue-700 dark:bg-blue-800 dark:text-blue-100">
                    {mineTagIds.length}
                  </span>
                )}
              </button>
            )}
          </div>

          {activeTab === 'mine' && mineTagIds.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5" aria-label={t('chat.list.tagFilter.selected', '已选筛选标签')}>
              {mineTagIds.map((id: string) => {
                const fromList = availableVisitorTags.find(tag => tag.id === id);
                const meta = mineTagMeta[id];
                const displayName = fromList?.display_name || fromList?.name || meta?.display_name || meta?.name || id;
                const color = (fromList?.color ?? meta?.color ?? null) as string | null;
                const tagForStyle = { id, display_name: displayName, color } as unknown as TagResponse;
                const hex = normalizeTagHex(tagForStyle.color);

                return (
                  <span
                    key={id}
                    className="inline-flex min-h-8 items-center gap-1 rounded-md py-1 pl-2 pr-1 text-xs leading-none"
                    style={{
                      backgroundColor: hexToRgba(hex, 0.12),
                      color: hex,
                    }}
                  >
                    <span className="truncate max-w-[140px]">{displayName}</span>
                    <button
                      type="button"
                      title={t('chat.list.tagFilter.remove', '删除标签')}
                      aria-label={t('chat.list.tagFilter.removeNamed', '删除标签 {{name}}', { name: displayName })}
                      className="ml-0.5 flex h-6 w-6 items-center justify-center rounded outline-none hover:bg-black/10 focus-visible:ring-2 focus-visible:ring-blue-500"
                      onClick={() => {
                        setMineTagIds(mineTagIds.filter((tagId: string) => tagId !== id));
                        setMineTagMeta(previous => {
                          const next = { ...previous };
                          delete next[id];
                          return next;
                        });
                      }}
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </span>
                );
              })}
              <button
                type="button"
                onClick={() => {
                  setMineTagIds([]);
                  setMineTagMeta({});
                }}
                className="min-h-8 rounded-md px-2 text-xs font-medium text-gray-600 outline-none hover:bg-gray-100 hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
              >
                {t('chat.list.tagFilter.clear', '清空')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 标签选择面板（Portal 到 body，避免被会话列表遮挡/裁剪） */}
      {isTagFilterOpen && tagPickerPos && typeof document !== 'undefined' && (
        <>
          {createPortal(
            <div
              ref={tagPickerRef}
              role="dialog"
              aria-label={t('chat.list.tagFilter.add', '添加筛选标签')}
              className="fixed z-[99999] rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl overflow-hidden"
              style={{ top: tagPickerPos.top, left: tagPickerPos.left, width: tagPickerPos.width }}
            >
              {/* Header */}
              <div className="px-3 py-2 border-b border-gray-200/70 dark:border-gray-700/70">
                <div className="text-xs font-medium text-gray-800 dark:text-gray-100">
                  {t('chat.list.tagFilter.add', '添加筛选标签')}
                </div>
                <div className="mt-2">
                  <input
                    value={tagSearch}
                    onChange={(e) => setTagSearch(e.target.value)}
                    placeholder={t('chat.list.tagFilter.searchPlaceholder', '搜索标签')}
                    className="w-full px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500/50"
                  />
                </div>
              </div>

              {/* List */}
              <div className="max-h-64 overflow-auto p-2">
                {isLoadingTags ? (
                  <div className="py-3 text-xs text-gray-500 dark:text-gray-400">
                    {t('chat.list.tagFilter.loading', '加载标签中...')}
                  </div>
                ) : availableVisitorTags.length === 0 ? (
                  <div className="py-3 text-xs text-gray-500 dark:text-gray-400">
                    {t('chat.list.tagFilter.empty', '暂无可用标签')}
                  </div>
                ) : (
                  <div className="space-y-1">
                    {availableVisitorTags
                      .filter(tg => !mineTagIds.includes(tg.id))
                      .filter(tg => {
                        const q = tagSearch.trim().toLowerCase();
                        if (!q) return true;
                        const label = (tg.display_name || tg.name || '').toLowerCase();
                        return label.includes(q);
                      })
                      .map((tg) => {
                        const label = tg.display_name || tg.name;
                        const hex = normalizeTagHex(tg.color);
                        return (
                          <button
                            key={tg.id}
                            type="button"
                            className="w-full text-left flex items-center justify-between gap-2 px-2 py-1.5 rounded hover:bg-gray-50 dark:hover:bg-gray-700/60"
                            onClick={() => {
                              setMineTagIds(mineTagIds.includes(tg.id) ? mineTagIds : [...mineTagIds, tg.id]);
                              setMineTagMeta(prev => ({
                                ...prev,
                                [tg.id]: { id: tg.id, display_name: tg.display_name, name: tg.name, color: tg.color ?? null },
                              }));
                              setIsTagFilterOpen(false);
                              setTagSearch('');
                            }}
                          >
                            <span className="flex items-center gap-2 min-w-0">
                              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: hex }} />
                              <span className="text-xs text-gray-700 dark:text-gray-200 truncate">{label}</span>
                            </span>
                            <Plus className="w-4 h-4 text-gray-400 flex-shrink-0" />
                          </button>
                        );
                      })}
                  </div>
                )}
              </div>
            </div>,
            document.body
          )}
        </>
      )}

      {/* Chat list */}
      <div 
        id="chat-list-panel"
        role="tabpanel"
        aria-labelledby={activeTab === 'recent' ? undefined : `chat-tab-${activeTab}`}
        ref={scrollContainerRef}
        className="flex-grow overflow-y-auto p-2 space-y-1" 
        style={{ height: 0 }}
        onScroll={handleScroll}
      >
        {activeTab === 'recent' ? (
          filteredRecentVisitors.length === 0 ? (
            <ChatListEmpty isSyncing={isLoading} />
          ) : (
            <>
              {filteredRecentVisitors.map((visitor: VisitorResponse) => (
                <OnlineVisitorListItem
                  key={visitor.id}
                  visitor={visitor}
                  isActive={activeChat?.channelId === `${visitor.id}-vtr`}
                  onClick={handleOnlineVisitorClick}
                />
              ))}
              {/* 加载更多提示 */}
              {isLoadingMore && (
                <div className="flex items-center justify-center py-3">
                  <div className="w-4 h-4 border-2 border-gray-300 dark:border-gray-600 border-t-blue-500 rounded-full animate-spin" />
                  <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">{t('common.loadingMore')}</span>
                </div>
              )}
              {/* 没有更多数据提示 */}
              {!hasMore && filteredRecentVisitors.length > 0 && (
                <div className="flex items-center justify-center py-3">
                  <span className="text-xs text-gray-400 dark:text-gray-500">{t('common.noMore')}</span>
                </div>
              )}
            </>
          )
        ) : filteredChats.length === 0 ? (
          <ChatListEmpty isSyncing={isLoading} />
        ) : (
          <>
            {filteredChats.map((chat: Chat) => (
              activeTab === 'unassigned' ? (
                <UnassignedChatListItem
                  key={chat.id}
                  chat={chat}
                  isActive={activeChat?.id === chat.id}
                  onClick={onChatSelect}
                />
              ) : (
                <ChatListItem
                  key={chat.id}
                  chat={chat}
                  isActive={activeChat?.id === chat.id}
                  onClick={handleChatClick}
                />
              )
            ))}
            {/* 加载更多提示 */}
            {isLoadingMore && (
              <div className="flex items-center justify-center py-3">
                <div className="w-4 h-4 border-2 border-gray-300 dark:border-gray-600 border-t-blue-500 rounded-full animate-spin" />
                <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">{t('common.loadingMore')}</span>
              </div>
            )}
            {/* 没有更多数据提示 */}
            {!hasMore && filteredChats.length > 0 && (activeTab === 'unassigned' || activeTab === 'all' || activeTab === 'manual') && (
              <div className="flex items-center justify-center py-3">
                <span className="text-xs text-gray-400 dark:text-gray-500">{t('common.noMore')}</span>
              </div>
            )}
          </>
        )}
      </div>

      {/* Onboarding Panel */}
      <OnboardingSidebarPanel />
    </div>
  );
};

// Wrap with React.memo to prevent unnecessary re-renders
const ChatList = React.memo(ChatListComponent);
ChatList.displayName = 'ChatList';

export default ChatList;
