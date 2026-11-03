import React, { useState, useCallback, useEffect, useRef } from 'react';
import type { Chat, PlatformType, ChannelVisitorExtra, VisitorServiceStatus } from '@/types';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Bot, LogOut, Loader2, ArrowRightLeft, ChevronDown, PanelRightOpen, User, RotateCcw, MoreHorizontal } from 'lucide-react';

import { getPlatformIconComponent, getPlatformLabel, toPlatformType, getPlatformColor } from '@/utils/platformUtils';
import { staffApi, type StaffListResponse } from '@/services/staffApi';
import type { StaffResponse } from '@/services/api';
import { useToast } from '@/hooks/useToast';
import { useChannelStore } from '@/stores/channelStore';
import { useChannelDisplay } from '@/hooks/useChannelDisplay';
import { useAuthStore } from '@/stores/authStore';
import { useChatStore } from '@/stores/chatStore';
import { chatMessagesApiService } from '@/services/chatMessagesApi';
import { sessionCommandTransport } from '@/services/sessionCommandTransport';
import {
  beginSessionCommand,
  checkPendingSessionCommand,
  readPendingSessionCommand,
  retryPendingSessionCommand,
  sessionCommandResolved,
} from '@/utils/sessionCommandRecovery';
import type { PendingSessionCommand, SessionCommandScope } from '@/types/sessionCommand';
import ConversationExport from './ConversationExport';
import SessionHandoffSummaryButton from './SessionHandoffSummaryButton';

/**
 * Props for the ChatHeader component
 */
export interface ChatHeaderProps {
  /** The active chat to display in the header */
  activeChat: Chat;
  /** Callback when chat is ended successfully (with channel info) */
  onEndChatSuccess?: (channelId: string, channelType: number) => void;
  /** Return to the conversation list on compact screens */
  onBackToList?: () => void;
  /** Open visitor information in a drawer on narrower screens */
  onOpenVisitorPanel?: () => void;
}

/**
 * Chat header component displaying visitor name and platform icon
 * Memoized to prevent unnecessary re-renders
 */
const ChatHeader: React.FC<ChatHeaderProps> = React.memo(({
  activeChat,
  onEndChatSuccess,
  onBackToList,
  onOpenVisitorPanel,
}) => {
  const { t } = useTranslation();
  const { showSuccess, showError } = useToast();
  const [isClosing, setIsClosing] = useState(false);
  const [isClearingMemory, setIsClearingMemory] = useState(false);
  const [isTransferring, setIsTransferring] = useState(false);
  const [pendingCommand, setPendingCommand] = useState<PendingSessionCommand | null>(null);
  const [isCheckingCommand, setIsCheckingCommand] = useState(false);
  const [showTransferMenu, setShowTransferMenu] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const actionsRef = useRef<HTMLDivElement>(null);
  const [staffList, setStaffList] = useState<StaffResponse[]>([]);
  const [isLoadingStaff, setIsLoadingStaff] = useState(false);
  const transferMenuRef = useRef<HTMLDivElement>(null);
  const updateChannelExtra = useChannelStore(state => state.updateChannelExtra);
  const deleteChat = useChatStore(state => state.deleteChat);
  const clearHistoricalMessages = useChatStore(state => state.clearHistoricalMessages);
  const setMessages = useChatStore(state => state.setMessages);
  const updateConversationPreview = useChatStore(state => state.updateConversationPreview);
  const currentUser = useAuthStore(state => state.user);
  
  // 判断是否是 agent 会话（channelId 以 -agent 结尾）
  const isAgentChat = activeChat.channelId?.endsWith('-agent') ?? false;
  const isAIChat = isAgentChat;
  
  // 使用 useChannelDisplay hook 获取频道展示信息
  const { name: channelName, extra } = useChannelDisplay({
    channelId: activeChat.channelId,
    channelType: activeChat.channelType,
    skipFetch: false, 
  });
  
  // 获取访客信息
  const visitorExtra = extra as ChannelVisitorExtra | undefined;
  const visitorId = visitorExtra?.id;
  const serviceStatus = visitorExtra?.service_status as VisitorServiceStatus | undefined;
  const assignedStaffId = visitorExtra?.assigned_staff_id;
  
  // 检查访客是否分配给当前坐席
  const isMyVisitor = !assignedStaffId || assignedStaffId === currentUser?.id;
  
  // 只有访客会话且状态为 active 且是我的访客时才显示结束聊天按钮
  const showEndChatButton = !isAIChat && visitorId && serviceStatus === 'active' && isMyVisitor;
  
  // 获取显示名称
  const displayName = channelName || '';

  const applySessionCommandSuccess = useCallback(() => {
    const channelId = activeChat.channelId;
    const channelType = activeChat.channelType;
    if (channelId && channelType !== undefined) {
      updateChannelExtra(channelId, channelType, {
        service_status: 'closed' as VisitorServiceStatus,
      });
      deleteChat(activeChat.id);
      onEndChatSuccess?.(channelId, channelType);
    }
  }, [activeChat.channelId, activeChat.channelType, activeChat.id, updateChannelExtra, deleteChat, onEndChatSuccess]);

  useEffect(() => {
    if (!visitorId || !currentUser?.id || !currentUser.project_id) {
      setPendingCommand(null);
      return;
    }
    const scope: SessionCommandScope = { projectId: currentUser.project_id, actorId: currentUser.id };
    const saved = readPendingSessionCommand(localStorage, scope, visitorId);
    setPendingCommand(saved);
    if (!saved) return;
    let cancelled = false;
    void checkPendingSessionCommand(localStorage, scope, visitorId, sessionCommandTransport).then(result => {
      if (cancelled || !result) return;
      if (result.state === 'committed') {
        setPendingCommand(null);
        applySessionCommandSuccess();
      }
    });
    return () => { cancelled = true; };
  }, [visitorId, currentUser?.id, currentUser?.project_id, applySessionCommandSuccess]);

  useEffect(() => {
    const handleResolved = (event: Event) => {
      const detail = (event as CustomEvent<{ visitorId: string; committed: boolean }>).detail;
      if (detail?.visitorId !== visitorId) return;
      setPendingCommand(null);
      if (detail.committed) applySessionCommandSuccess();
    };
    window.addEventListener(sessionCommandResolved, handleResolved);
    return () => window.removeEventListener(sessionCommandResolved, handleResolved);
  }, [visitorId, applySessionCommandSuccess]);

  // 结束聊天处理
  const handleEndChat = useCallback(async () => {
    if (!visitorId || !currentUser?.id || !currentUser.project_id || isClosing || pendingCommand) return;
    
    setIsClosing(true);
    try {
      const result = await beginSessionCommand(
        localStorage, { projectId: currentUser.project_id, actorId: currentUser.id },
        visitorId, 'close', null, sessionCommandTransport,
      );
      if (result.state === 'pending') {
        setPendingCommand(result.command);
        showError(t('chat.header.commandPending', '操作结果待确认。请查询结果，不要创建新操作。'));
        return;
      }
      if (result.state === 'rejected') {
        showError(result.message);
        return;
      }
      showSuccess(t('chat.header.endChatSuccess', '会话已结束'));
      applySessionCommandSuccess();
    } catch (error: unknown) {
      console.error('Failed to close session:', error);
      showError(error instanceof Error ? error.message : t('chat.header.endChatError', '结束会话失败'));
    } finally {
      setIsClosing(false);
    }
  }, [visitorId, currentUser?.id, currentUser?.project_id, isClosing, pendingCommand, showSuccess, showError, t, applySessionCommandSuccess]);

  // 清除记忆处理
  const handleClearMemory = useCallback(async () => {
    if (!activeChat.channelId || activeChat.channelType === undefined || isClearingMemory) return;
    
    const confirmClear = window.confirm(t('chat.header.clearMemoryConfirm', '确定要清除此会话的 AI 记忆吗？清除后 AI 将不再记得之前的对话内容。'));
    if (!confirmClear) return;

    setIsClearingMemory(true);
    try {
      // 1. 先调用接口远程清除
      await chatMessagesApiService.clearChatMemory({
        channel_id: activeChat.channelId,
        channel_type: activeChat.channelType,
      });

      // 2. 如果远程调用成功，再清空本地缓存的聊天记录
      clearHistoricalMessages(activeChat.channelId, activeChat.channelType);
      setMessages([]);
      updateConversationPreview(activeChat.channelId, activeChat.channelType, '');
      
      showSuccess(t('chat.header.clearMemorySuccess', '记忆已清除'));
    } catch (error: any) {
      // 3. 如果远程调用失败需要提醒用户
      console.error('Failed to clear memory:', error);
      showError(error?.message || t('chat.header.clearMemoryError', '清除记忆失败'));
    } finally {
      setIsClearingMemory(false);
    }
  }, [activeChat.channelId, activeChat.channelType, isClearingMemory, clearHistoricalMessages, setMessages, updateConversationPreview, showSuccess, showError, t]);

  // 加载坐席列表
  const loadStaffList = useCallback(async () => {
    if (isLoadingStaff) return;
    setIsLoadingStaff(true);
    try {
      const response: StaffListResponse = await staffApi.listStaff({ limit: 50 });
      // 过滤掉当前用户和未激活的坐席
      const filteredStaff = response.data.filter(
        staff => staff.id !== currentUser?.id && staff.is_active !== false
      );
      setStaffList(filteredStaff);
    } catch (error) {
      console.error('Failed to load staff list:', error);
      showError(t('chat.header.loadStaffError', '获取坐席列表失败'));
    } finally {
      setIsLoadingStaff(false);
    }
  }, [isLoadingStaff, currentUser?.id, showError, t]);

  // 打开转接菜单
  const handleTransferClick = useCallback(() => {
    if (!showTransferMenu) {
      loadStaffList();
    }
    setShowTransferMenu(!showTransferMenu);
  }, [showTransferMenu, loadStaffList]);

  // 执行转接
  const handleTransferToStaff = useCallback(async (targetStaffId: string, staffName: string) => {
    if (!visitorId || !currentUser?.id || !currentUser.project_id || isTransferring || pendingCommand) return;
    
    setIsTransferring(true);
    setShowTransferMenu(false);
    try {
      const result = await beginSessionCommand(
        localStorage, { projectId: currentUser.project_id, actorId: currentUser.id },
        visitorId, 'transfer', targetStaffId, sessionCommandTransport,
      );
      if (result.state === 'pending') {
        setPendingCommand(result.command);
        showError(t('chat.header.commandPending', '操作结果待确认。请查询结果，不要创建新操作。'));
        return;
      }
      if (result.state === 'rejected') {
        showError(result.message);
        return;
      }
      showSuccess(t('chat.header.transferSuccess', '已转接给 {{name}}', { name: staffName }));
      applySessionCommandSuccess();
    } catch (error: unknown) {
      console.error('Failed to transfer session:', error);
      showError(error instanceof Error ? error.message : t('chat.header.transferError', '转接失败'));
    } finally {
      setIsTransferring(false);
    }
  }, [visitorId, currentUser?.id, currentUser?.project_id, isTransferring, pendingCommand, showSuccess, showError, t, applySessionCommandSuccess]);

  const handlePendingCommand = useCallback(async (retry: boolean) => {
    if (!visitorId || !currentUser?.id || !currentUser.project_id || isCheckingCommand) return;
    setIsCheckingCommand(true);
    const scope: SessionCommandScope = { projectId: currentUser.project_id, actorId: currentUser.id };
    try {
      const result = retry
        ? await retryPendingSessionCommand(localStorage, scope, visitorId, sessionCommandTransport)
        : await checkPendingSessionCommand(localStorage, scope, visitorId, sessionCommandTransport);
      if (result?.state === 'committed') {
        setPendingCommand(null);
        showSuccess(t('chat.header.commandCommitted', '原操作已完成'));
        applySessionCommandSuccess();
      } else if (result?.state === 'rejected') {
        setPendingCommand(null);
        showError(result.message);
      } else {
        setPendingCommand(readPendingSessionCommand(localStorage, scope, visitorId));
        showError(t('chat.header.commandStillPending', '尚未查到原操作结果，请稍后查询。'));
      }
    } finally {
      setIsCheckingCommand(false);
    }
  }, [visitorId, currentUser?.id, currentUser?.project_id, isCheckingCommand, showSuccess, showError, t, applySessionCommandSuccess]);

  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (transferMenuRef.current && !transferMenuRef.current.contains(event.target as Node)) {
        setShowTransferMenu(false);
      }
    };
    if (showTransferMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showTransferMenu]);

  useEffect(() => {
    if (!showActions) return;
    const dismiss = (event: MouseEvent) => {
      if (event.target instanceof Node && !actionsRef.current?.contains(event.target)) setShowActions(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowActions(false);
    };
    document.addEventListener('mousedown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [showActions]);
  
  return (
    <header className="shrink-0 px-6 max-[899px]:px-2 py-3 border-b border-gray-200/80 dark:border-gray-700/80 flex gap-2 justify-between items-center bg-white/60 dark:bg-gray-800/60 backdrop-blur-lg sticky top-0 z-20">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {onBackToList && (
          <button
            type="button"
            onClick={onBackToList}
            aria-label={t('chat.header.backToList', '返回会话列表')}
            className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-md text-gray-600 outline-none hover:bg-gray-100 hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-blue-500 max-[899px]:flex dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
        <h2 className="min-w-0 truncate text-lg font-semibold text-gray-800 dark:text-gray-200 flex items-center">
          <span className="min-w-0 truncate">{displayName}</span>
          {isAgentChat ? (
            <span title={t('chat.header.agentTooltip', 'AI员工会话')}>
              <Bot className="w-4 h-4 inline-block ml-1.5 -mt-0.5 text-purple-500 dark:text-purple-400" />
            </span>
          ) : (
            (() => {
              const fromExtra: PlatformType | undefined = visitorExtra?.platform_type;
              const type = fromExtra ?? toPlatformType(activeChat.platform);
              const IconComp = getPlatformIconComponent(type);
              const label = getPlatformLabel(type);
              return (
                <span title={label}>
                  <IconComp size={16} className={`w-3.5 h-3.5 inline-block ml-1 -mt-0.5 ${getPlatformColor(type)}`} />
                </span>
              );
            })()
          )}
        </h2>
      </div>
      <div className="flex shrink-0 items-center gap-1">
      {!isAIChat && onOpenVisitorPanel && (
        <button
          type="button"
          onClick={onOpenVisitorPanel}
          className="hidden min-h-9 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-gray-700 outline-none hover:bg-gray-100 focus-visible:ring-2 focus-visible:ring-blue-500 max-[1199px]:inline-flex dark:text-gray-200 dark:hover:bg-gray-700"
        >
          <PanelRightOpen className="h-4 w-4" aria-hidden="true" />
          <span className="max-[899px]:sr-only">{t('visitor.ui.panelTitle', '访客信息')}</span>
        </button>
      )}
      <div ref={actionsRef} className="chat-header-actions">
      <button type="button" aria-label={t('mobileChat.actions')} aria-expanded={showActions} onClick={() => setShowActions(value => !value)} className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700 min-[900px]:hidden"><MoreHorizontal className="h-5 w-5" /></button>
      <div aria-label={t('mobileChat.actions')} className={`chat-header-action-list ${showActions ? 'flex' : 'hidden'} min-[900px]:flex items-center gap-1`}>
      {/* 操作按钮 - 只对访客会话显示 */}
      {!isAIChat && currentUser?.role === 'admin' && activeChat.channelId && activeChat.channelType !== undefined && (
        <ConversationExport key={`${activeChat.channelType}:${activeChat.channelId}`} channelId={activeChat.channelId} channelType={activeChat.channelType} />
      )}
      {showEndChatButton && <SessionHandoffSummaryButton key={visitorId} visitorId={visitorId} />}
      {showEndChatButton && (
        <div className="flex items-center gap-1">
          {pendingCommand && (
            <div className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">
              <span>{t('chat.header.commandPendingLabel', '结果待确认')}</span>
              <button type="button" onClick={() => void handlePendingCommand(false)} disabled={isCheckingCommand} className="rounded px-2 py-1 hover:bg-amber-50 dark:hover:bg-amber-900/20 disabled:opacity-50">
                {t('chat.header.checkCommand', '查询结果')}
              </button>
              <button type="button" onClick={() => void handlePendingCommand(true)} disabled={isCheckingCommand} className="rounded px-2 py-1 hover:bg-amber-50 dark:hover:bg-amber-900/20 disabled:opacity-50">
                {t('chat.header.retryCommand', '重试原操作')}
              </button>
            </div>
          )}
          {/* 转接按钮 */}
          <div className="relative" ref={transferMenuRef}>
            <button
              onClick={handleTransferClick}
              disabled={isTransferring || !!pendingCommand}
              className="flex items-center gap-1 px-2 py-1 text-xs text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded transition-colors duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              title={t('chat.header.transferTooltip', '转接给其他客服')}
            >
              {isTransferring ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <ArrowRightLeft className="w-3.5 h-3.5" />
              )}
              <span>{t('chat.header.transfer', '转接')}</span>
              <ChevronDown className="w-3 h-3" />
            </button>
            {/* 坐席选择下拉菜单 */}
            {showTransferMenu && (
              <div className="absolute right-0 top-full mt-1 w-48 max-[899px]:static max-[899px]:w-full bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-20 py-1 max-h-60 overflow-y-auto">
                {isLoadingStaff ? (
                  <div className="flex items-center justify-center py-3">
                    <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                  </div>
                ) : staffList.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                    {t('chat.header.noAvailableStaff', '暂无可用坐席')}
                  </div>
                ) : (
                  staffList.map(staff => (
                    <button
                      key={staff.id}
                      onClick={() => handleTransferToStaff(staff.id, staff.nickname || staff.username)}
                      className="w-full flex items-center gap-2 px-3 py-2 text-xs text-left text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    >
                      {staff.avatar_url ? (
                        <img src={staff.avatar_url} alt="" className="w-5 h-5 rounded-full object-cover" />
                      ) : (
                        <User className="w-5 h-5 text-gray-400" />
                      )}
                      <span className="truncate">{staff.nickname || staff.username}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          {/* 结束聊天按钮 */}
          <button
            onClick={handleEndChat}
            disabled={isClosing || !!pendingCommand}
            className="flex items-center gap-1 px-2 py-1 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            title={t('chat.header.endChatTooltip', '结束当前会话')}
          >
            {isClosing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <LogOut className="w-3.5 h-3.5" />
            )}
            <span>{t('chat.header.endChat', '结束聊天')}</span>
          </button>
        </div>
      )}
      {/* 操作按钮 - AI 会话显示清除记忆 */}
      {isAIChat && (
        <div className="flex items-center gap-1">
          <button
            onClick={handleClearMemory}
            disabled={isClearingMemory}
            className="flex items-center gap-1 px-2 py-1 text-xs text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20 rounded transition-colors duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            title={t('chat.header.clearMemoryTooltip', '清除 AI 对话记忆，重新开始对话')}
          >
            {isClearingMemory ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RotateCcw className="w-3.5 h-3.5" />
            )}
            <span>{t('chat.header.clearMemory', '清除记忆')}</span>
          </button>
        </div>
      )}
      </div>
      </div>
      </div>
    </header>
  );
});

ChatHeader.displayName = 'ChatHeader';

export default ChatHeader;
