import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, Volume2, Eye, MessageSquare, Users, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { useNotification } from '@/hooks/useNotification';
import Toggle from '@/components/ui/Toggle';

/**
 * 通知设置页面
 * 
 * 功能：
 * - 通知权限状态显示和申请按钮
 * - 桌面通知开关
 * - 声音通知开关
 * - 通知场景配置
 * - 测试通知按钮
 */
const NotificationSettings: React.FC = () => {
  const { t } = useTranslation();
  const {
    permission,
    isSupported,
    isRequesting,
    preferences,
    requestPermission,
    updatePreferences,
    sendTestNotification,
  } = useNotification();
  const [testResult, setTestResult] = useState<boolean | null>(null);
  const canSendDesktop = isSupported && permission === 'granted';
  // 保留用户偏好；浏览器权限只决定当前是否生效，不反写偏好。
  const desktopEnabled = canSendDesktop && preferences.notificationEnabled;
  const desktopStatus = !isSupported
    ? t('settings.notifications.notSupported')
    : !canSendDesktop && preferences.notificationEnabled
      ? t('settings.notifications.toggles.desktop.awaitingPermission')
      : desktopEnabled
        ? t('settings.notifications.toggles.desktop.enabled')
        : t('settings.notifications.toggles.desktop.disabled');

  // 权限状态对应的图标和颜色
  const permissionConfig = {
    granted: {
      icon: CheckCircle,
      color: 'text-green-500',
      bgColor: 'bg-green-50 dark:bg-green-900/30',
      borderColor: 'border-green-200 dark:border-green-800',
      label: t('settings.notifications.permission.granted', '已授权'),
    },
    denied: {
      icon: XCircle,
      color: 'text-red-500',
      bgColor: 'bg-red-50 dark:bg-red-900/30',
      borderColor: 'border-red-200 dark:border-red-800',
      label: t('settings.notifications.permission.denied', '已拒绝'),
    },
    default: {
      icon: AlertCircle,
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-50 dark:bg-yellow-900/30',
      borderColor: 'border-yellow-200 dark:border-yellow-800',
      label: t('settings.notifications.permission.default', '未设置'),
    },
    unsupported: {
      icon: AlertCircle,
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-50 dark:bg-yellow-900/30',
      borderColor: 'border-yellow-200 dark:border-yellow-800',
      label: t('settings.notifications.permission.unsupported'),
    },
  };

  const currentPermission = isSupported ? permissionConfig[permission] : permissionConfig.unsupported;
  const PermissionIcon = currentPermission.icon;

  // 处理请求权限
  const handleRequestPermission = async () => {
    await requestPermission();
  };

  // 通知场景设置项
  const notificationScenarios = [
    {
      key: 'notifyOnBackground' as const,
      icon: Eye,
      title: t('settings.notifications.scenarios.background.title', '后台通知'),
      description: t('settings.notifications.scenarios.background.description', '当页面不可见（切换标签页或最小化）时发送通知'),
      enabled: preferences.notifyOnBackground,
    },
    {
      key: 'notifyOnOtherConversation' as const,
      icon: MessageSquare,
      title: t('settings.notifications.scenarios.otherConversation.title', '其他会话消息'),
      description: t('settings.notifications.scenarios.otherConversation.description', '当收到非当前查看会话的消息时发送通知'),
      enabled: preferences.notifyOnOtherConversation,
    },
    {
      key: 'notifyOnNewVisitor' as const,
      icon: Users,
      title: t('settings.notifications.scenarios.newVisitor.title', '新访客通知'),
      description: t('settings.notifications.scenarios.newVisitor.description', '当有新访客进入排队时发送通知'),
      enabled: preferences.notifyOnNewVisitor,
    },
  ];

  return (
    <div className="p-6 space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center gap-2">
        <Bell className="w-5 h-5 text-gray-600 dark:text-gray-400" />
        <h2 className="text-lg font-medium text-gray-800 dark:text-gray-200">
          {t('settings.notifications.title', '消息通知')}
        </h2>
      </div>

      {/* 浏览器不支持提示 */}
      {!isSupported && (
        <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-yellow-500" />
            <p className="text-sm text-yellow-700 dark:text-yellow-300">
              {t('settings.notifications.notSupported', '您的浏览器不支持桌面通知功能')}
            </p>
          </div>
        </div>
      )}

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 space-y-6">
        {/* 权限状态 */}
        <div>
          <div className="text-sm font-medium text-gray-800 dark:text-gray-200 mb-3">
            {t('settings.notifications.permission.title', '通知权限')}
          </div>
          <div className={`p-4 rounded-lg border ${currentPermission.bgColor} ${currentPermission.borderColor}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <PermissionIcon className={`w-5 h-5 ${currentPermission.color}`} />
                <div>
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                    {currentPermission.label}
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    {!isSupported
                      ? t('settings.notifications.notSupported')
                      : permission === 'granted'
                      ? t('settings.notifications.permission.grantedDesc', '可以接收桌面通知')
                      : permission === 'denied'
                      ? t('settings.notifications.permission.deniedDesc', '请在浏览器设置中允许通知权限')
                      : t('settings.notifications.permission.defaultDesc', '点击按钮请求通知权限')}
                  </p>
                </div>
              </div>
              {permission !== 'granted' && isSupported && (
                <button
                  onClick={handleRequestPermission}
                  disabled={isRequesting || permission === 'denied'}
                  className={`
                    px-4 py-2 text-sm font-medium rounded-lg transition-colors
                    ${permission === 'denied'
                      ? 'bg-gray-100 dark:bg-gray-700 text-gray-400 dark:text-gray-500 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700 text-white'
                    }
                  `}
                >
                  {isRequesting
                    ? t('settings.notifications.permission.requesting', '请求中...')
                    : t('settings.notifications.permission.request', '请求权限')}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* 通知开关 */}
        <div className="pt-6 border-t border-gray-200 dark:border-gray-700">
          <div className="text-sm font-medium text-gray-800 dark:text-gray-200 mb-4">
            {t('settings.notifications.toggles.title', '通知设置')}
          </div>
          <div className="space-y-4">
            {/* 桌面通知开关 */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-lg">
                  <Bell className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                    {t('settings.notifications.toggles.desktop.title', '桌面通知')}
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('settings.notifications.toggles.desktop.description', '收到新消息时在系统通知中心显示通知')}
                  </p>
                  <p role="status" className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    {desktopStatus}
                  </p>
                </div>
              </div>
              <Toggle
                checked={desktopEnabled}
                onChange={(checked) => updatePreferences({ notificationEnabled: checked })}
                disabled={!canSendDesktop}
                aria-label={t('settings.notifications.toggles.desktop.title', '桌面通知')}
              />
            </div>

            {/* 声音提醒开关 */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-purple-50 dark:bg-purple-900/30 rounded-lg">
                  <Volume2 className="w-5 h-5 text-purple-600 dark:text-purple-400" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                    {t('settings.notifications.toggles.sound.title', '声音提醒')}
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('settings.notifications.toggles.sound.description', '收到新消息时播放提示音')}
                  </p>
                </div>
              </div>
              <Toggle
                checked={preferences.notificationSound}
                onChange={(checked) => updatePreferences({ notificationSound: checked })}
                aria-label={t('settings.notifications.toggles.sound.title', '声音提醒')}
              />
            </div>
          </div>
        </div>

        {/* 通知场景 */}
        <div className="pt-6 border-t border-gray-200 dark:border-gray-700">
          <div className="text-sm font-medium text-gray-800 dark:text-gray-200 mb-4">
            {t('settings.notifications.scenarios.title', '通知场景')}
          </div>
          <div className="space-y-4">
            {notificationScenarios.map((scenario) => {
              const ScenarioIcon = scenario.icon;
              return (
                <div key={scenario.key} className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-gray-50 dark:bg-gray-700 rounded-lg">
                      <ScenarioIcon className="w-5 h-5 text-gray-600 dark:text-gray-400" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                        {scenario.title}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {scenario.description}
                      </p>
                    </div>
                  </div>
                  <Toggle
                    checked={scenario.enabled}
                    onChange={(checked) => updatePreferences({ [scenario.key]: checked })}
                    disabled={!preferences.notificationEnabled && !preferences.notificationSound}
                    aria-label={scenario.title}
                  />
                </div>
              );
            })}
          </div>
        </div>

        {/* 测试通知 */}
        <div className="pt-6 border-t border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                {t('settings.notifications.test.title', '测试通知')}
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {t('settings.notifications.test.description', '发送一条测试通知以验证功能是否正常')}
              </p>
            </div>
            <button
              onClick={() => setTestResult(sendTestNotification())}
              disabled={!canSendDesktop}
              className={`
                px-4 py-2 text-sm font-medium rounded-lg transition-colors
                ${!canSendDesktop
                  ? 'bg-gray-100 dark:bg-gray-700 text-gray-400 dark:text-gray-500 cursor-not-allowed'
                  : 'bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300'
                }
              `}
            >
              {t('settings.notifications.test.button', '发送测试')}
            </button>
          </div>
          {canSendDesktop && testResult !== null && (
            <p role="status" className="mt-3 text-sm text-gray-600 dark:text-gray-300">
              {t(testResult ? 'settings.notifications.test.sent' : 'settings.notifications.test.failed')}
            </p>
          )}
        </div>
      </div>

      {/* 提示信息 */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <p className="text-sm text-blue-700 dark:text-blue-300">
          💡 {t('settings.notifications.tip', '提示：通知功能需要浏览器授权。如果您拒绝了权限请求，需要在浏览器设置中手动允许本站点的通知权限。')}
        </p>
      </div>
    </div>
  );
};

export default NotificationSettings;
