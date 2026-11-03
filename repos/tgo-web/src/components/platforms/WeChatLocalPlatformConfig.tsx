import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IoLogoWechat } from 'react-icons/io5';
import PlatformAISettings from './PlatformAISettings';
import { useToast } from '@/hooks/useToast';
import { getApiBaseUrl } from '@/services/api';
import { platformsApiService, type WeChatLocalStatus } from '@/services/platformsApi';
import { usePlatformStore } from '@/stores/platformStore';
import type { Platform } from '@/types';
import { showApiError, showSuccess } from '@/utils/toastHelpers';

export default function WeChatLocalPlatformConfig({ platform }: { platform: Platform }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const updatePlatform = usePlatformStore(state => state.updatePlatform);
  const isSaving = usePlatformStore(state => state.isUpdating);
  const [status, setStatus] = useState<WeChatLocalStatus | 'error' | null>(null);
  const [agentIds, setAgentIds] = useState<string[]>(platform.agent_id ? [platform.agent_id] : []);
  const disabled = platform.status === 'disabled';
  const agentChanged = (agentIds[0] ?? null) !== (platform.agent_id ?? null);

  useEffect(() => {
    if (disabled) return;
    let disposed = false;
    let timer: number;
    const refresh = async () => {
      try {
        const latest = await platformsApiService.getWeChatLocalStatus(platform.id);
        if (!disposed) setStatus(latest);
      } catch {
        if (!disposed) setStatus('error');
      } finally {
        if (!disposed) timer = window.setTimeout(refresh, 5000);
      }
    };
    setStatus(null);
    void refresh();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [disabled, platform.id]);

  const connected = !disabled && status !== 'error' && status?.connected === true;
  const lastSeen = !disabled && status !== 'error' ? status?.last_seen_at : null;
  const scopedEnabled = !disabled && status !== 'error' && status?.scoped_enabled === true;
  const statusLabel = disabled ? t('platforms.wechatLocal.disabled', '已停用')
    : status === 'error' ? t('platforms.wechatLocal.unavailable', '状态暂不可用')
    : status === null ? t('platforms.wechatLocal.checking', '正在检查')
    : connected ? t('platforms.wechatLocal.online', '桥接在线')
    : t('platforms.wechatLocal.offline', '待连接');
  const apiBase = new URL(getApiBaseUrl(), window.location.origin).href.replace(/\/$/, '');
  const backendUrl = new URL('/platforms', window.location.origin).href;
  const downloadUrl = new URL('/downloads/wechat-local-bridge-v2.zip', window.location.origin).href;
  const startCommand = `.\\start-wechat-bridge.cmd "${apiBase}" "${platform.id}"`;
  const prompt = t('platforms.wechatLocal.v2Prompt',
    '请在这台 Windows 电脑上配置域见客服的个人微信桥接。后台 {{backendUrl}}，工具下载 {{downloadUrl}}，客服 API {{apiBase}}。请直接绑定当前渠道 {{channelId}}。\n\n请下载并解压，完成环境检查并从解压目录运行 {{startCommand}}。需要我登录微信、在本机终端粘贴渠道密钥或选择账号目录时，请告诉我具体操作。工具会自动检查微信窗口。绑定后打开「接待范围」，由我勾选联系人、分组和群聊，决定何时启用。哪一步做不到，请告诉我从下面哪一步继续。请只汇报进度和状态。', {
      backendUrl, downloadUrl, apiBase, channelId: platform.id, startCommand,
    });

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      showSuccess(showToast, t('platforms.wechatLocal.copied', '已复制'));
    } catch (error) {
      showApiError(showToast, error);
    }
  };

  const save = async () => {
    try {
      await updatePlatform(platform.id, {
        agent_id: agentIds[0] ?? null,
        status: disabled ? 'disabled' : 'connected',
      });
      showSuccess(showToast, t('platforms.wechatLocal.saved', '已保存客服设置'));
    } catch (error) {
      showApiError(showToast, error);
    }
  };

  return (
    <div className="min-w-0 flex-1 overflow-y-auto bg-gray-50 p-5 dark:bg-gray-950 sm:p-8">
      <div className="mx-auto max-w-3xl space-y-5">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <IoLogoWechat size={28} className="shrink-0 text-green-600" aria-hidden />
            <div className="min-w-0">
              <h1 className="break-words text-xl font-semibold text-gray-900 dark:text-white">
                {platform.display_name || platform.name}
              </h1>
              <p className="text-sm text-gray-500">{t('platforms.wechatLocal.label', '个人微信')}</p>
            </div>
          </div>
          <span role="status" className={`rounded-full px-3 py-1 text-sm ${connected
            ? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
            : 'bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-200'}`}>
            {statusLabel}
          </span>
        </header>
        <section className="space-y-4 rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
          <h2 className="font-semibold">{t('platforms.wechatLocal.v2Assistant', '把提示词交给办公助手')}</h2>
          {!scopedEnabled && (
            <p className="text-sm text-amber-800 dark:text-amber-200">
              {t('platforms.wechatLocal.v2Preview', '好友和群聊接待正在验收，目前可以预览配置步骤。')}
            </p>
          )}
          <p className="text-sm text-gray-600 dark:text-gray-300">
            {t('platforms.wechatLocal.v2AssistantHint', '提示词已填好当前渠道。请在运行微信的 Windows 电脑上配置，最后由你选择接待范围。')}
          </p>
          <button type="button" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white" onClick={() => void copy(prompt)}>
            {t('platforms.wechatLocal.v2CopyPrompt', '复制配置提示词')}
          </button>
          {lastSeen && <p className="text-sm text-gray-500">
            {t('platforms.wechatLocal.lastSeen', '最近上报 {{time}}', { time: new Date(lastSeen).toLocaleString() })}
          </p>}
        </section>
        <section className="space-y-3 rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
          <h2 className="font-semibold">{t('platforms.wechatLocal.v2Manual', '助手做不到时，自己接着做')}</h2>
          <ol className="list-decimal space-y-3 pl-5 text-sm text-gray-600 dark:text-gray-300">
            <li>
              <a className="text-blue-600 underline dark:text-blue-300" href={downloadUrl} download>
                {t('platforms.wechatLocal.v2Download', '下载并打开工具')}
              </a>
              {' '}{t('platforms.wechatLocal.v2Open', '解压后在工具目录打开 PowerShell，粘贴启动命令。')}
              <button type="button" className="ml-2 rounded-md border px-2 py-1" onClick={() => void copy(startCommand)}>
                {t('platforms.wechatLocal.v2CopyStart', '复制启动命令')}
              </button>
            </li>
            <li>{t('platforms.wechatLocal.v2Login', '登录 Windows 微信，工具会自动检查窗口。')}</li>
            <li>
              {t('platforms.wechatLocal.v2Bind', '绑定当前渠道。在本机终端粘贴渠道密钥，打开「接待范围」，勾选联系人、分组或群聊，再启用。')}
              <button type="button" className="ml-2 rounded-md border px-2 py-1" disabled={!platform.config.apiKey}
                onClick={() => void copy(platform.config.apiKey ?? '')}>
                {t('platforms.wechatLocal.copyKey', '复制渠道密钥')}
              </button>
            </li>
          </ol>
        </section>
        <section className="space-y-3">
          <PlatformAISettings
            platform={platform}
            agentIds={agentIds}
            onAgentIdsChange={setAgentIds}
          />
          {agentChanged && <button type="button" disabled={isSaving} onClick={() => void save()}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {t('common.save', '保存')}
          </button>}
        </section>
      </div>
    </div>
  );
}
