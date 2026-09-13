/**
 * useNotification - 通知管理 Hook
 * 
 * 用于：
 * - 管理通知权限状态
 * - 请求通知权限
 * - 检查浏览器支持
 */

import { useState, useEffect, useCallback } from 'react';
import { 
  notificationService, 
  type NotificationPermission,
  type NotificationPreferences,
  DEFAULT_NOTIFICATION_PREFERENCES
} from '@/services/notificationService';
import { useUIStore } from '@/stores/uiStore';

export interface UseNotificationReturn {
  /** 通知权限状态 */
  permission: NotificationPermission;
  /** 浏览器是否支持通知 */
  isSupported: boolean;
  /** 是否正在请求权限 */
  isRequesting: boolean;
  /** 通知偏好设置 */
  preferences: NotificationPreferences;
  /** 请求通知权限 */
  requestPermission: () => Promise<NotificationPermission>;
  /** 更新通知偏好 */
  updatePreferences: (prefs: Partial<NotificationPreferences>) => void;
  /** 发送测试通知 */
  sendTestNotification: () => boolean;
}

/**
 * 通知管理 Hook
 */
export const useNotification = (): UseNotificationReturn => {
  const [permission, setPermission] = useState<NotificationPermission>(
    notificationService.getPermission()
  );
  const [isRequesting, setIsRequesting] = useState(false);
  const isSupported = notificationService.isSupported();

  // 从 uiStore 获取通知偏好设置
  const uiPreferences = useUIStore(state => state.preferences);
  const updateUIPreferences = useUIStore(state => state.updatePreferences);

  // 将 uiStore 的偏好映射到通知偏好
  const preferences: NotificationPreferences = {
    notificationEnabled: uiPreferences.notificationEnabled ?? DEFAULT_NOTIFICATION_PREFERENCES.notificationEnabled,
    notificationSound: uiPreferences.notificationSound ?? DEFAULT_NOTIFICATION_PREFERENCES.notificationSound,
    notifyOnBackground: uiPreferences.notifyOnBackground ?? DEFAULT_NOTIFICATION_PREFERENCES.notifyOnBackground,
    notifyOnOtherConversation: uiPreferences.notifyOnOtherConversation ?? DEFAULT_NOTIFICATION_PREFERENCES.notifyOnOtherConversation,
    notifyOnNewVisitor: uiPreferences.notifyOnNewVisitor ?? DEFAULT_NOTIFICATION_PREFERENCES.notifyOnNewVisitor,
  };

  // 监听权限变化（某些浏览器支持）
  useEffect(() => {
    if (!isSupported) return;

    // 初始获取权限状态
    setPermission(notificationService.getPermission());

    // 监听页面可见性变化时重新检查权限
    const handleVisibilityChange = () => {
      setPermission(notificationService.getPermission());
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleVisibilityChange);
    };
  }, [isSupported]);

  /**
   * 请求通知权限
   */
  const requestPermission = useCallback(async (): Promise<NotificationPermission> => {
    if (!isSupported) {
      return 'denied';
    }

    setIsRequesting(true);
    try {
      const result = await notificationService.requestPermission();
      setPermission(result);
      return result;
    } finally {
      setIsRequesting(false);
    }
  }, [isSupported]);

  /**
   * 更新通知偏好设置
   */
  const updatePreferences = useCallback((prefs: Partial<NotificationPreferences>) => {
    updateUIPreferences(prefs);
  }, [updateUIPreferences]);

  /**
   * 发送测试通知
   */
  const sendTestNotification = useCallback(() => {
    return notificationService.sendTestNotification(preferences.notificationSound);
  }, [preferences.notificationSound]);

  return {
    permission,
    isSupported,
    isRequesting,
    preferences,
    requestPermission,
    updatePreferences,
    sendTestNotification,
  };
};

export default useNotification;
