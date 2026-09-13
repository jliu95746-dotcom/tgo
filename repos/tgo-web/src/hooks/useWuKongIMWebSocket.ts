import { useEffect, useCallback, useState } from 'react';
import { useAuthStore } from '../stores/authStore';
import {
  wukongimWebSocketService,
  ConnectionStatus,
  WuKongIMWebSocketConfig,
  WsSendResult
} from '../services/wukongimWebSocket';
import { WuKongIMApiService } from '../services/wukongimApi';

export interface UseWuKongIMWebSocketReturn {
  connectionStatus: ConnectionStatus;
  connect: () => Promise<void>;
  disconnect: () => void;
  sendMessage: (channelId: string, channelType: number, payload: any, clientMsgNo: string) => Promise<WsSendResult>;
  isConnected: boolean;
  isConnecting: boolean;
  error?: string;
}

/**
 * Custom hook for accessing WuKongIM WebSocket connection status
 * This hook no longer manages the connection itself - connection is managed
 * by the centralized WebSocketManager component to prevent multiple connections.
 * 
 * Use this hook to:
 * - Get connection status
 * - Manually trigger connection/disconnection
 * - Send messages through WebSocket
 */
export const useWuKongIMWebSocket = (): UseWuKongIMWebSocketReturn => {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>(
    wukongimWebSocketService.getConnectionStatus()
  );

  // Use stable selectors to prevent unnecessary re-renders
  const token = useAuthStore(state => state.token);
  const user = useAuthStore(state => state.user);

  /**
   * Handle connection status changes
   */
  const handleConnectionStatus = useCallback((status: ConnectionStatus) => {
    console.log('🔌 Hook: Connection status changed:', status);
    setConnectionStatus(status);
  }, []);

  /**
   * Handle WebSocket errors
   */
  const handleError = useCallback((error: any) => {
    console.error('🔌 Hook: WebSocket error:', error);
  }, []);

  /**
   * Connect to WebSocket (manual connection trigger)
   */
  const connect = useCallback(async () => {
    if (!token) {
      throw new Error('Authentication token is required');
    }

    if (!user?.id) {
      throw new Error('User ID is required for WebSocket connection');
    }

    // Generate dynamic UID using user ID with "-staff" suffix
    const uid = `${user.id}-staff`;

    console.log('🔌 Hook: Manual connect requested', {
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
      console.error('🔌 Hook: Manual connect failed:', error);
      throw error;
    }
  }, [token, user?.id]);

  /**
   * Disconnect from WebSocket
   */
  const disconnect = useCallback(() => {
    console.log('🔌 Hook: Manual disconnect requested');
    wukongimWebSocketService.safeDisconnect();
  }, []);

  /**
   * Send message through WebSocket
   * @param channelId Target channel ID
   * @param channelType Target channel type
   * @param payload Message payload
   * @param clientMsgNo Client message number for deduplication (required)
   * @returns Promise with send result containing messageId, messageSeq, and reasonCode
   */
  const sendMessage = useCallback(async (
    channelId: string,
    channelType: number,
    payload: any,
    clientMsgNo: string
  ): Promise<WsSendResult> => {
    try {
      const ack = await wukongimWebSocketService.sendMessage(channelId, channelType, payload, clientMsgNo);
      console.log('🔌 Hook: Message sent successfully:', ack);
      return ack;
    } catch (error) {
      console.error('🔌 Hook: Failed to send message:', error);
      
      // Check if this is a state inconsistency error
      if (error instanceof Error && 
          (error.message.includes('state inconsistency') || 
           error.message.includes('WebSocket not initialized'))) {
        console.log('🔌 Hook: State inconsistency detected, attempting force reconnect');
        
        // Attempt force reconnection if we have the required auth data
        if (token && user?.id) {
          try {
            const uid = `${user.id}-staff`;
            const serverUrl = await WuKongIMApiService.resolveWebSocketUrl(uid);
            const config: WuKongIMWebSocketConfig = {
              serverUrl,
              uid,
              token,
            };

            await wukongimWebSocketService.forceReconnect(config);
            console.log('🔌 Hook: Force reconnect successful, retrying message send');
            
            // Retry the send operation once after reconnect
            return await wukongimWebSocketService.sendMessage(channelId, channelType, payload, clientMsgNo);
          } catch (reconnectError) {
            console.error('🔌 Hook: Force reconnect failed:', reconnectError);
            throw new Error('连接状态不一致，重连失败，请刷新页面');
          }
        } else {
          throw new Error('连接状态不一致，请重新登录');
        }
      }
      
      throw error;
    }
  }, [token, user?.id]);

  /**
   * Setup event listeners on mount
   */
  useEffect(() => {
    const unsubscribeStatus = wukongimWebSocketService.onConnectionStatus(handleConnectionStatus);
    const unsubscribeError = wukongimWebSocketService.onError(handleError);

    // Get initial connection status
    setConnectionStatus(wukongimWebSocketService.getConnectionStatus());

    return () => {
      unsubscribeStatus();
      unsubscribeError();
    };
  }, [handleConnectionStatus, handleError]);

  return {
    connectionStatus,
    connect,
    disconnect,
    sendMessage,
    isConnected: connectionStatus.isConnected,
    isConnecting: connectionStatus.isConnecting,
    error: connectionStatus.error,
  };
};

  /**
   * Lightweight hook for sending messages with automatic WebSocket/REST API fallback
   * Does NOT trigger WebSocket connection management to avoid circular dependencies
   */
  export const useMessageSender = () => {
    const token = useAuthStore(state => state.token);
    const user = useAuthStore(state => state.user);

    const sendMessage = useCallback(async (
      channelId: string,
      channelType: number,
      content: string,
      clientMsgNo: string,
      messageType: number = 1
    ) => {
      // Create proper WuKongIM payload format
      const payload = {
        type: messageType, // 1 for text message
        content: content.trim(),
        timestamp: Date.now(),
      };

      // Check WebSocket connection status directly from service
      const connectionStatus = wukongimWebSocketService.getConnectionStatus();
      const isConnected = connectionStatus.isConnected;

      try {
        if (!isConnected) {
          throw new Error('WebSocket not connected - cannot send message');
        }

        // Send via WebSocket only - no fallback
        console.log('Sending message via WebSocket');
        return await wukongimWebSocketService.sendMessage(channelId, channelType, payload, clientMsgNo);
        
      } catch (error) {
        console.error('WebSocket message sending failed:', error);
        
        // Check if this is a state inconsistency error and attempt force reconnect
        if (error instanceof Error && 
            (error.message.includes('state inconsistency') || 
             error.message.includes('WebSocket not initialized')) &&
            token && user?.id) {
          console.log('🔌 MessageSender: State inconsistency detected, attempting force reconnect');
          
          try {
            const uid = `${user.id}-staff`;
            const serverUrl = await WuKongIMApiService.resolveWebSocketUrl(uid);
            const config: WuKongIMWebSocketConfig = {
              serverUrl,
              uid,
              token,
            };

            await wukongimWebSocketService.forceReconnect(config);
            console.log('🔌 MessageSender: Force reconnect successful, retrying message send');
            
            // Retry the send operation once after reconnect
            return await wukongimWebSocketService.sendMessage(channelId, channelType, payload, clientMsgNo);
          } catch (reconnectError) {
            console.error('🔌 MessageSender: Force reconnect failed:', reconnectError);
            throw new Error('连接状态不一致，重连失败，请刷新页面');
          }
        }
        
        // Do not fallback to REST API - let the error propagate
        throw error;
      }
    }, [ token, user?.id]);

    // Get connection status directly from service to avoid hook dependencies
    const connectionStatus = wukongimWebSocketService.getConnectionStatus();

    return {
      sendMessage,
      isConnected: connectionStatus.isConnected,
    };
  };
