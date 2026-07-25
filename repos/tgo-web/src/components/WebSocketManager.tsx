import React, { useEffect } from 'react';
import { createMixedStreamParser, type MixedStreamParser } from '@json-render/core';
import { useAuthStore } from '../stores/authStore';
import { useChatStore } from '../stores/chatStore';
import { useChannelStore } from '../stores/channelStore';
import { useUIStore } from '../stores/uiStore';
import {
  wukongimWebSocketService,
  ConnectionStatus,
  WuKongIMWebSocketConfig,
  type VisitorPresenceEvent,
  type VisitorProfileUpdatedEvent,
} from '../services/wukongimWebSocket';
import { WuKongIMApiService } from '../services/wukongimApi';
import { notificationService, type NotificationPreferences, DEFAULT_NOTIFICATION_PREFERENCES } from '../services/notificationService';
import { Message } from '../types';
import { isWebSocketAutoConnectDisabled } from '@/utils/config';

/**
 * Ensure ```spec fence markers are on their own lines.
 * The LLM sometimes emits "text```spec" on the same line, but the parser
 * only recognises fence openers at the start of a line (after trim).
 */
function normalizeSpecFences(chunk: string): string {
  return chunk.replace(/([^\n])```spec/g, '$1\n```spec');
}

/**
 * Centralized WebSocket manager component
 * This component should be rendered ONCE at the app level to manage
 * the single WebSocket connection and prevent multiple connection attempts.
 */
export const WebSocketManager: React.FC = () => {
  // Use stable selectors to prevent unnecessary re-renders
  const token = useAuthStore(state => state.token);
  const user = useAuthStore(state => state.user);
  const handleRealtimeMessage = useChatStore(state => state.handleRealtimeMessage);
  const appendMixedPart = useChatStore(state => state.appendMixedPart);
  const markStreamMessageEnd = useChatStore(state => state.markStreamMessageEnd);
  const markStreamMessageFinish = useChatStore(state => state.markStreamMessageFinish);
  const activeChat = useChatStore(state => state.activeChat);

  // Per-clientMsgNo MixedStreamParser instances
  const parsersRef = React.useRef<Map<string, MixedStreamParser>>(new Map());
  
  // Get connection/notification actions from uiStore
  const uiPreferences = useUIStore(state => state.preferences);
  const setConnectionStatus = useUIStore(state => state.setConnectionStatus);

  /**
   * Handle notification click - navigate to the conversation
   * Using window.location because WebSocketManager may be rendered outside Router context
   */
  const handleNotificationClick = React.useCallback((channelId: string, channelType: number) => {
    console.log('🔔 WebSocket Manager: Notification clicked, navigating to:', { channelId, channelType });
    window.location.href = `/chat/${channelType}/${channelId}`;
  }, []);

  /**
   * Handle incoming real-time messages
   */
  const handleMessage = React.useCallback((message: Message) => {
    const channelId = message.channelId;
    const isActiveConversation = activeChat?.channelId === channelId;

    console.log('🔌 WebSocket Manager: Received real-time message', {
      messageId: message.id,
      content: `${message.content.substring(0, 50)}...`,
      sender: message.fromInfo?.name,
      type: message.type,
      channelId,
      timestamp: message.timestamp,
      isActiveConversation,
      activeChannelId: activeChat?.channelId
    });

    if (!channelId) {
      console.warn('🔌 WebSocket Manager: Message missing channel_id, cannot process');
      return;
    }

    try {
      console.log('🔌 WebSocket Manager: Delegating to chat store handleRealtimeMessage');
      handleRealtimeMessage(message);
      console.log('🔌 WebSocket Manager: Real-time message processing completed successfully');
      
      // Check and send notification
      const notificationPreferences: NotificationPreferences = {
        notificationEnabled: uiPreferences.notificationEnabled ?? DEFAULT_NOTIFICATION_PREFERENCES.notificationEnabled,
        notificationSound: uiPreferences.notificationSound ?? DEFAULT_NOTIFICATION_PREFERENCES.notificationSound,
        notifyOnBackground: uiPreferences.notifyOnBackground ?? DEFAULT_NOTIFICATION_PREFERENCES.notifyOnBackground,
        notifyOnOtherConversation: uiPreferences.notifyOnOtherConversation ?? DEFAULT_NOTIFICATION_PREFERENCES.notifyOnOtherConversation,
        notifyOnNewVisitor: uiPreferences.notifyOnNewVisitor ?? DEFAULT_NOTIFICATION_PREFERENCES.notifyOnNewVisitor,
      };
      
      notificationService.checkAndNotify(
        message,
        activeChat,
        notificationPreferences,
        handleNotificationClick
      );
    } catch (error) {
      console.error('🔌 WebSocket Manager: Error in handleRealtimeMessage:', error);
    }
  }, [
    activeChat,
    handleRealtimeMessage,
    uiPreferences,
    handleNotificationClick
  ]);

  // Track if this is a reconnection (not the initial connection)
  const wasConnectedRef = React.useRef(false);
  const forceSyncConversations = useChatStore(state => state.forceSyncConversations);

  /**
   * Handle connection status changes
   */
  const handleConnectionStatus = React.useCallback((status: ConnectionStatus) => {
    console.log('🔌 WebSocket Manager: Connection status changed:', status);
    
    // Sync status to UI store
    setConnectionStatus(status);
    
    // If we're now connected and we were previously connected (i.e., this is a reconnection)
    if (status.isConnected) {
      if (wasConnectedRef.current) {
        // This is a reconnection, sync conversations
        console.log('🔌 WebSocket Manager: Reconnected, syncing conversations');
        forceSyncConversations().catch(err => {
          console.error('🔌 WebSocket Manager: Failed to sync after reconnection:', err);
        });
        const currentActiveChat = useChatStore.getState().activeChat;
        if (currentActiveChat?.channelId && currentActiveChat.channelType != null) {
          useChatStore.getState()
            .loadNewerHistory(
              currentActiveChat.channelId,
              currentActiveChat.channelType,
            )
            .catch(err => {
              console.error(
                'Failed to reconcile active chat after reconnection:',
                err,
              );
            });
        }
      }
      wasConnectedRef.current = true;
    } else if (!status.isConnecting) {
      // Connection lost (not just connecting)
      // Keep wasConnectedRef.current as true to detect next reconnection
    }
  }, [forceSyncConversations, setConnectionStatus]);

  /**
   * Handle WebSocket errors
   */
  const handleError = React.useCallback((error: any) => {
    console.error('🔌 WebSocket Manager: WebSocket error:', error);
  }, []);

  /**
   * Handle AI stream messages (incremental updates).
   * Uses MixedStreamParser to split text and json-render patches in order.
   */
  const handleStreamMessage = React.useCallback((clientMsgNo: string, content: string) => {
    try {
      let parser = parsersRef.current.get(clientMsgNo);
      if (!parser) {
        parser = createMixedStreamParser({
          onText: (text) => {
            // Append '\n' that was stripped by the line-based parser so
            // merged text parts retain their original line breaks.
            appendMixedPart(clientMsgNo, { type: 'text', text: `${text}\n` });
          },
          onPatch: (patch) => {
            appendMixedPart(clientMsgNo, { type: 'data-spec', data: { type: 'patch', patch } });
          },
        });
        parsersRef.current.set(clientMsgNo, parser);
      }
      // Normalise so ```spec is always at the start of its own line
      parser.push(normalizeSpecFences(content));
    } catch (error) {
      console.error('🤖 WebSocket Manager: Error in stream message handler:', error);
    }
  }, [appendMixedPart]);

  /**
   * Handle AI stream end events
   * @param clientMsgNo - The client message number
   * @param error - Optional error message from the stream end event
   */
  const handleStreamEnd = React.useCallback((clientMsgNo: string, error?: string) => {
    try {
      // Flush any remaining buffered content in the parser
      const parser = parsersRef.current.get(clientMsgNo);
      if (parser) {
        parser.flush();
        parsersRef.current.delete(clientMsgNo);
      }
      markStreamMessageEnd(clientMsgNo, error);
      if (error) {
        console.log('🤖 WebSocket Manager: Stream message ended with error:', error);
      } else {
        console.log('🤖 WebSocket Manager: Stream message marked as ended');
      }
    } catch (err) {
      console.error('🤖 WebSocket Manager: Error marking stream message end:', err);
    }
  }, [markStreamMessageEnd]);

  /**
   * Handle AI stream finish events (entire stream message completed)
   */
  const handleStreamFinish = React.useCallback((clientMsgNo: string) => {
    try {
      markStreamMessageFinish(clientMsgNo);
      const currentActiveChat = useChatStore.getState().activeChat;
      if (currentActiveChat?.channelId && currentActiveChat.channelType != null) {
        void useChatStore.getState().loadNewerHistory(
          currentActiveChat.channelId,
          currentActiveChat.channelType,
        );
      }
      console.log('🤖 WebSocket Manager: Stream message finished (all channels done)');
    } catch (err) {
      console.error('🤖 WebSocket Manager: Error marking stream message finish:', err);
    }
  }, [markStreamMessageFinish]);

  /**
   * Handle visitor presence events (visitor.online / visitor.offline)
   */
  const handlePresenceEvent = React.useCallback((presence: VisitorPresenceEvent) => {
    try {
      const { channelId, channelType, isOnline, timestamp } = presence;
      if (!channelId || !Number.isFinite(channelType)) return;

      const channelStore = useChannelStore.getState();
      // Update cached channel info presence fields (no fetch)
      channelStore.updateVisitorOnlineStatus(channelId, channelType, isOnline, timestamp ?? undefined);

      // Apply to chat list if the channel exists in cache
      const updatedInfo = channelStore.getChannel(channelId, channelType);
      if (updatedInfo) {
        useChatStore.getState().applyChannelInfo(channelId, channelType, updatedInfo);
      }
    } catch (error) {
      console.error('🔌 WebSocket Manager: Error handling presence event:', error);
    }
  }, []);

  /**
   * Handle visitor.profile.updated events
   */
  const handleVisitorProfileUpdated = React.useCallback(async (evt: VisitorProfileUpdatedEvent) => {
    try {
      const { channelId, channelType } = evt;
      if (!channelId || !Number.isFinite(channelType)) return;

      const channelStore = useChannelStore.getState();
      // Force refresh from API
      await channelStore.refreshChannel({ channel_id: channelId, channel_type: channelType });

      // Apply to chat list if the channel exists in cache
      const updatedInfo = channelStore.getChannel(channelId, channelType);
      if (updatedInfo) {
        useChatStore.getState().applyChannelInfo(channelId, channelType, updatedInfo);
      }
    } catch (error) {
      console.error('🔌 WebSocket Manager: Error handling visitor.profile.updated:', error);
    }
  }, []);



  /**
   * Connect to WebSocket
   */
  const connect = React.useCallback(async () => {
    if (!token) {
      console.log('🔌 WebSocket Manager: No token available, skipping connection');
      return;
    }

    if (!user?.id) {
      console.log('🔌 WebSocket Manager: No user ID available, skipping connection');
      return;
    }

    // Generate dynamic UID using user ID with "-staff" suffix
    const uid = `${user.id}-staff`;

    console.log('🔌 WebSocket Manager: Connecting with dynamic UID', {
      userId: user.id,
      uid,
      hasToken: !!token
    });

    // Resolve server URL dynamically via backend route (with env fallback)
    const serverUrl = await WuKongIMApiService.resolveWebSocketUrl(uid);
    const config: WuKongIMWebSocketConfig = {
      serverUrl,
      uid,
      token,
    };

    try {
      await wukongimWebSocketService.init(config);
    } catch (error) {
      console.error('🔌 WebSocket Manager: Failed to connect:', error);
    }
  }, [token, user?.id]);

  /**
   * Setup event listeners on mount
   */
  useEffect(() => {
    console.log('🔌 WebSocket Manager: Setting up event listeners (including stream + presence)');

    const unsubscribeMessage = wukongimWebSocketService.onMessage(handleMessage);
    const unsubscribeStatus = wukongimWebSocketService.onConnectionStatus(handleConnectionStatus);
    const unsubscribeError = wukongimWebSocketService.onError(handleError);
    const unsubscribeStreamMessage = wukongimWebSocketService.onStreamMessage(handleStreamMessage);
    const unsubscribeStreamEnd = wukongimWebSocketService.onStreamEnd(handleStreamEnd);
    const unsubscribeStreamFinish = wukongimWebSocketService.onStreamFinish(handleStreamFinish);
    const unsubscribePresence = wukongimWebSocketService.onVisitorPresence(handlePresenceEvent);
    const unsubscribeProfileUpdated = wukongimWebSocketService.onVisitorProfileUpdated(handleVisitorProfileUpdated);


    return () => {
      console.log('🔌 WebSocket Manager: Cleaning up event listeners (including stream + presence)');
      unsubscribeMessage();
      unsubscribeStatus();
      unsubscribeError();
      unsubscribeStreamMessage();
      unsubscribeStreamEnd();
      unsubscribeStreamFinish();
      unsubscribePresence();
      unsubscribeProfileUpdated();
    };
  }, [handleMessage, handleConnectionStatus, handleError, handleStreamMessage, handleStreamEnd, handleStreamFinish, handlePresenceEvent, handleVisitorProfileUpdated]);

  /**
   * Auto-connect when token and user are available
   */
  useEffect(() => {
    // Check if auto-connect is disabled via runtime/build-time configuration
    if (isWebSocketAutoConnectDisabled()) {
      console.log('🔌 WebSocket Manager: Auto-connect is disabled via environment variable');
      return;
    }

    if (token && user?.id) {
      console.log('🔌 WebSocket Manager: Token and user available, attempting auto-connect');
      connect().catch(error => {
        console.error('🔌 WebSocket Manager: Auto-connect failed:', error);
      });
    } else {
      console.log('🔌 WebSocket Manager: Token or user not available 1111', {
        hasToken: !!token,
        hasUser: !!user?.id
      });
    }
  }, [token, user?.id, connect]);

  /**
   * Cleanup on unmount
   */
  useEffect(() => {
    return () => {
      console.log('🔌 WebSocket Manager: Component unmounting, disconnecting WebSocket');
      wukongimWebSocketService.safeDisconnect();
    };
  }, []);

  // This component doesn't render anything
  return null;
};
