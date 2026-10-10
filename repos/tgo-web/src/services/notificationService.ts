/**
 * NotificationService - 浏览器通知服务
 * 
 * 负责：
 * - 检查通知条件（页面可见性、当前会话、消息来源）
 * - 发送浏览器桌面通知
 * - 播放通知铃声
 * - 点击通知跳转到对应会话
 */

import { MessagePayloadType, type Message, type Chat } from '@/types';
import { MESSAGE_SENDER_TYPE } from '@/constants';
import { useAuthStore } from '@/stores/authStore';
import i18n from '@/i18n';

/**
 * 通知偏好设置接口
 */
export interface NotificationPreferences {
  /** 是否启用桌面通知 */
  notificationEnabled: boolean;
  /** 是否播放声音 */
  notificationSound: boolean;
  /** 页面不可见时通知 */
  notifyOnBackground: boolean;
  /** 其他会话消息通知 */
  notifyOnOtherConversation: boolean;
  /** 新访客通知 */
  notifyOnNewVisitor: boolean;
}

/**
 * 默认通知设置
 */
export const DEFAULT_NOTIFICATION_PREFERENCES: NotificationPreferences = {
  notificationEnabled: true,
  notificationSound: true,
  notifyOnBackground: true,
  notifyOnOtherConversation: true,
  notifyOnNewVisitor: true,
};

/**
 * 通知权限状态
 */
export type NotificationPermission = 'default' | 'granted' | 'denied';

/**
 * 通知服务类
 */
class NotificationService {
  private audio: HTMLAudioElement | null = null;
  private audioContext: AudioContext | null = null;
  private lastNotificationTime = 0;
  private lastQueueNotice: { account: string; time: number } | null = null;
  private readonly DEBOUNCE_MS = 1000; // 防抖时间：1秒内只发送一次通知
  private readonly NOTIFICATION_DURATION = 5000; // 通知显示时间：5秒

  constructor() {
    this.initAudio();
  }

  /**
   * 初始化音频对象
   * 优先尝试加载音频文件，失败则使用 Web Audio API 生成音效
   */
  private initAudio(): void {
    if (typeof window === 'undefined') return;
    
    try {
      // 尝试加载音频文件
      this.audio = new Audio('/sounds/notification.mp3');
      this.audio.preload = 'auto';
      this.audio.volume = 0.5;
      
      // 监听加载错误，准备使用 Web Audio API 作为备选
      this.audio.addEventListener('error', () => {
        console.log('🔔 NotificationService: Audio file not found, will use Web Audio API');
        this.audio = null;
      });
    } catch (error) {
      console.warn('🔔 NotificationService: Failed to initialize audio:', error);
    }
  }

  /**
   * 获取或创建 AudioContext
   */
  private getAudioContext(): AudioContext | null {
    if (typeof window === 'undefined') return null;
    
    if (!this.audioContext) {
      try {
        const AudioContextClass = window.AudioContext || (
          window as Window & { webkitAudioContext?: typeof AudioContext }
        ).webkitAudioContext;
        if (AudioContextClass) {
          this.audioContext = new AudioContextClass();
        }
      } catch (error) {
        console.warn('🔔 NotificationService: Web Audio API not supported:', error);
      }
    }
    
    return this.audioContext;
  }

  /**
   * 使用 Web Audio API 播放简单的通知音效
   * 播放两个短促的音调，类似于常见的消息提示音
   */
  private async playWebAudioNotification(): Promise<void> {
    const ctx = this.getAudioContext();
    if (!ctx) return;

    try {
      // 确保 AudioContext 处于运行状态
      if (ctx.state === 'suspended') {
        await ctx.resume();
      }

      const currentTime = ctx.currentTime;
      
      // 创建两个短促的音调
      const playTone = (frequency: number, startTime: number, duration: number) => {
        const oscillator = ctx.createOscillator();
        const gainNode = ctx.createGain();
        
        oscillator.connect(gainNode);
        gainNode.connect(ctx.destination);
        
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(frequency, startTime);
        
        // 音量淡入淡出
        gainNode.gain.setValueAtTime(0, startTime);
        gainNode.gain.linearRampToValueAtTime(0.3, startTime + 0.02);
        gainNode.gain.linearRampToValueAtTime(0.3, startTime + duration - 0.02);
        gainNode.gain.linearRampToValueAtTime(0, startTime + duration);
        
        oscillator.start(startTime);
        oscillator.stop(startTime + duration);
      };

      // 播放两个音调：C5 和 E5
      playTone(523.25, currentTime, 0.1);       // C5
      playTone(659.25, currentTime + 0.12, 0.15); // E5
      
      console.log('🔔 NotificationService: Web Audio notification played');
    } catch (error) {
      console.warn('🔔 NotificationService: Failed to play Web Audio notification:', error);
    }
  }

  /**
   * 获取当前通知权限状态
   */
  getPermission(): NotificationPermission {
    if (typeof window === 'undefined' || !('Notification' in window)) {
      return 'denied';
    }
    return Notification.permission as NotificationPermission;
  }

  /**
   * 检查浏览器是否支持通知
   */
  isSupported(): boolean {
    return typeof window !== 'undefined' && 'Notification' in window;
  }

  /**
   * 请求通知权限
   */
  async requestPermission(): Promise<NotificationPermission> {
    if (!this.isSupported()) {
      console.warn('🔔 NotificationService: Browser does not support notifications');
      return 'denied';
    }

    try {
      const permission = await Notification.requestPermission();
      console.log('🔔 NotificationService: Permission result:', permission);
      return permission as NotificationPermission;
    } catch (error) {
      console.error('🔔 NotificationService: Failed to request permission:', error);
      return this.getPermission();
    }
  }

  /**
   * 检查页面是否可见
   */
  private isPageVisible(): boolean {
    if (typeof document === 'undefined') return true;
    return !document.hidden;
  }

  /**
   * 检查消息是否来自当前活跃会话
   */
  private isFromActiveChat(message: Message, activeChat: Chat | null): boolean {
    if (!activeChat) return false;
    return message.channelId === activeChat.channelId && 
           message.channelType === activeChat.channelType;
  }

  /**
   * 检查是否是自己发送的消息
   */
  private isOwnMessage(message: Message): boolean {
    // staff 类型的消息是自己发送的
    const currentUid = `${useAuthStore.getState().user?.id}-staff`;
    return message.fromUid === currentUid;
  }

  /**
   * 检查是否是系统消息
   */
  private isSystemMessage(message: Message): boolean {
    return message.type === MESSAGE_SENDER_TYPE.SYSTEM;
  }

  private isHumanHandoff(message: Message): boolean {
    return this.isSystemMessage(message)
      && (message.payloadType ?? message.payload?.type) === MessagePayloadType.HUMAN_HANDOFF_REQUESTED;
  }

  /**
   * 检查是否应该发送通知
   * 
   * 触发通知的条件（参考钉钉、企业微信、Intercom）：
   * 1. 页面不可见时收到新消息
   * 2. 页面可见但消息来自非当前活跃会话
   * 3. 不是自己发送的消息
   * 4. 默认不通知系统消息
   */
  shouldNotify(
    message: Message,
    activeChat: Chat | null,
    preferences: NotificationPreferences
  ): boolean {
    // 1. 检查通知是否启用
    if (!preferences.notificationEnabled) {
      return false;
    }

    // 2. 检查是否是自己发送的消息
    if (this.isOwnMessage(message)) {
      return false;
    }

    // 3. 系统消息默认不通知
    if (this.isSystemMessage(message) && !this.isHumanHandoff(message)) {
      return false;
    }

    // 4. 检查权限
    if (this.getPermission() !== 'granted') {
      return false;
    }

    const pageVisible = this.isPageVisible();
    const isFromActive = this.isFromActiveChat(message, activeChat);

    if (pageVisible && this.isHumanHandoff(message)) return true;

    // 5. 页面不可见时
    if (!pageVisible && preferences.notifyOnBackground) {
      return true;
    }

    // 6. 页面可见但消息来自其他会话
    if (pageVisible && !isFromActive && preferences.notifyOnOtherConversation) {
      return true;
    }

    return false;
  }

  /**
   * 检查是否应该播放声音
   */
  shouldPlaySound(
    message: Message,
    activeChat: Chat | null,
    preferences: NotificationPreferences
  ): boolean {
    // 声音播放的条件与通知类似，但额外检查声音设置
    if (!preferences.notificationSound) {
      return false;
    }

    // 自己的消息不播放声音
    if (this.isOwnMessage(message)) {
      return false;
    }

    // 系统消息不播放声音
    if (this.isSystemMessage(message) && !this.isHumanHandoff(message)) {
      return false;
    }

    const pageVisible = this.isPageVisible();
    const isFromActive = this.isFromActiveChat(message, activeChat);

    if (pageVisible && this.isHumanHandoff(message)) return true;

    // 页面不可见时播放声音
    if (!pageVisible && preferences.notifyOnBackground) {
      return true;
    }

    // 页面可见但消息来自其他会话时播放声音
    if (pageVisible && !isFromActive && preferences.notifyOnOtherConversation) {
      return true;
    }

    return false;
  }

  /**
   * 发送桌面通知
   */
  sendDesktopNotification(
    message: Message,
    onClick?: (channelId: string, channelType: number) => void
  ): void {
    if (!this.isSupported() || this.getPermission() !== 'granted') {
      return;
    }

    // 防抖：1秒内只发送一次通知
    const now = Date.now();
    if (now - this.lastNotificationTime < this.DEBOUNCE_MS) {
      console.log('🔔 NotificationService: Debouncing notification');
      return;
    }
    this.lastNotificationTime = now;

    try {
      // 构建通知内容
      const senderName = this.isHumanHandoff(message)
        ? i18n.t('chat.status.humanHandoffRequested') : message.fromInfo?.name || '访客';
      const content = this.truncateContent(message.content, 50);
      
      const notification = new Notification(senderName, {
        body: content,
        icon: message.fromInfo?.avatar || '/yujian-logo.svg',
        tag: `msg-${message.channelId}`, // 相同 tag 的通知会合并
        requireInteraction: false, // 自动关闭
        silent: true, // 我们自己控制声音
      });

      // 点击通知跳转到对应会话
      notification.onclick = () => {
        window.focus();
        if (onClick && message.channelId && message.channelType !== undefined) {
          onClick(message.channelId, message.channelType);
        }
        notification.close();
      };

      // 自动关闭通知
      setTimeout(() => {
        notification.close();
      }, this.NOTIFICATION_DURATION);

      console.log('🔔 NotificationService: Notification sent for channel:', message.channelId);
    } catch (error) {
      console.error('🔔 NotificationService: Failed to send notification:', error);
    }
  }

  /**
   * 播放通知音效
   * 优先使用音频文件，失败时使用 Web Audio API 生成音效
   */
  async playNotificationSound(): Promise<void> {
    // 优先尝试使用音频文件
    if (this.audio) {
      try {
        this.audio.currentTime = 0;
        await this.audio.play();
        console.log('🔔 NotificationService: Sound played (audio file)');
        return;
      } catch {
        // 音频文件播放失败，使用 Web Audio API
        console.log('🔔 NotificationService: Audio file playback failed, using Web Audio API');
      }
    }

    // 使用 Web Audio API 作为备选
    try {
      await this.playWebAudioNotification();
    } catch (error) {
      console.warn('🔔 NotificationService: Failed to play notification sound:', error);
    }
  }

  /**
   * 检查并发送通知（统一入口）
   */
  checkAndNotify(
    message: Message,
    activeChat: Chat | null,
    preferences: NotificationPreferences,
    onNotificationClick?: (channelId: string, channelType: number) => void,
    onInApp?: (title: string, body: string) => void,
  ): void {
    if (this.isHumanHandoff(message) && preferences.notificationEnabled && this.isPageVisible()) {
      onInApp?.(i18n.t('chat.status.humanHandoffRequested'), message.content);
    }
    // 检查是否应该发送桌面通知
    if (this.shouldNotify(message, activeChat, preferences)) {
      this.sendDesktopNotification(message, onNotificationClick);
    }

    // 检查是否应该播放声音
    if (this.shouldPlaySound(message, activeChat, preferences)) {
      this.playNotificationSound();
    }
  }

  /** Queue events contain aggregate counts only, never a visitor identity. */
  checkAndNotifyQueue(
    raw: unknown,
    preferences: NotificationPreferences,
    onClick: () => void,
    onInApp: (title: string, body: string) => void,
  ): void {
    const { user, isAuthenticated } = useAuthStore.getState();
    if (!isAuthenticated || !user?.id || !user.project_id || !raw || typeof raw !== 'object') return;
    const event = raw as Record<string, unknown>;
    if (event.reason !== undefined && event.reason !== 'entered') return;
    if (event.project_id !== user.project_id
      || typeof event.waiting_count !== 'number'
      || !Number.isSafeInteger(event.waiting_count) || event.waiting_count <= 0) return;
    if (!preferences.notifyOnNewVisitor
      || (!preferences.notificationEnabled && !preferences.notificationSound)
      || (!this.isPageVisible() && !preferences.notifyOnBackground)) return;

    const account = `${user.project_id}:${user.id}`;
    const now = Date.now();
    if (this.lastQueueNotice?.account === account
      && now - this.lastQueueNotice.time < this.DEBOUNCE_MS) return;
    this.lastQueueNotice = { account, time: now };

    // The producer may emit before its DB transaction commits. Do not present
    // the event's aggregate as a freshly verified queue count.
    const title = i18n.t('settings.notifications.queue.title');
    const body = i18n.t('settings.notifications.queue.body');
    let desktopSent = false;
    if (preferences.notificationEnabled && this.isSupported() && this.getPermission() === 'granted') {
      try {
        const notification = new Notification(title, {
          body, icon: '/yujian-logo.svg', tag: `queue-${user.project_id}`, silent: true,
        });
        notification.onclick = () => {
          const current = useAuthStore.getState();
          if (current.isAuthenticated && current.user?.id === user.id
            && current.user.project_id === user.project_id) {
            window.focus();
            onClick();
          }
          notification.close();
        };
        setTimeout(() => notification.close(), this.NOTIFICATION_DURATION);
        desktopSent = true;
      } catch (error) {
        console.warn('Queue desktop notification unavailable:', error);
      }
    }
    if (!desktopSent) onInApp(title, body);
    if (preferences.notificationSound) void this.playNotificationSound();
  }

  /**
   * 发送测试通知
   */
  sendTestNotification(soundEnabled = true): boolean {
    if (!this.isSupported() || this.getPermission() !== 'granted') {
      console.warn('🔔 NotificationService: Cannot send test notification - permission not granted');
      return false;
    }

    try {
      const notification = new Notification(i18n.t('settings.notifications.test.title'), {
        body: i18n.t('settings.notifications.test.body'),
        icon: '/yujian-logo.svg',
        tag: 'test-notification',
        requireInteraction: false,
        silent: true,
      });

      setTimeout(() => {
        notification.close();
      }, this.NOTIFICATION_DURATION);

      // 播放声音
      if (soundEnabled) void this.playNotificationSound();
      return true;
    } catch (error) {
      console.error('🔔 NotificationService: Failed to send test notification:', error);
      return false;
    }
  }

  /**
   * 截断内容
   */
  private truncateContent(content: string, maxLength: number): string {
    if (!content) return '';
    if (content.length <= maxLength) return content;
    return `${content.substring(0, maxLength)}...`;
  }

  /**
   * 更新页面标题显示未读数
   */
  updatePageTitle(unreadCount: number, originalTitle?: string): void {
    if (typeof document === 'undefined') return;
    
    const baseTitle = originalTitle || '域见客服';
    
    if (unreadCount > 0) {
      document.title = `(${unreadCount}) ${baseTitle}`;
    } else {
      document.title = baseTitle;
    }
  }
}

// 导出单例
export const notificationService = new NotificationService();
