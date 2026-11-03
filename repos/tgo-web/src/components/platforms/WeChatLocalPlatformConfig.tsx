import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IoLogoWechat } from 'react-icons/io5';
import PlatformAISettings from './PlatformAISettings';
import { useToast } from '@/hooks/useToast';
import { getApiBaseUrl } from '@/services/api';
import { platformsApiService, type WeChatLocalStatus } from '@/services/platformsApi';
import { usePlatformStore } from '@/stores/platformStore';
import type { Platform, PlatformAIMode } from '@/types';
import { showApiError, showSuccess } from '@/utils/toastHelpers';

export default function WeChatLocalPlatformConfig({ platform }: { platform: Platform }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const updatePlatform = usePlatformStore(state => state.updatePlatform);
  const isSaving = usePlatformStore(state => state.isUpdating);
  const [status, setStatus] = useState<WeChatLocalStatus | 'error' | null>(null);
  const [agentIds, setAgentIds] = useState<string[]>(platform.agent_id ? [platform.agent_id] : []);
  const [aiMode, setAiMode] = useState<PlatformAIMode>(platform.ai_mode ?? 'auto');
  const [timeout, setTimeoutSeconds] = useState(platform.fallback_to_ai_timeout ?? null);
  const disabled = platform.status === 'disabled';

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
  const statusLabel = disabled ? t('platforms.wechatLocal.disabled', '已停用')
    : status === 'error' ? t('platforms.wechatLocal.unavailable', '状态暂不可用')
    : status === null ? t('platforms.wechatLocal.checking', '正在检查')
    : connected ? t('platforms.wechatLocal.online', '桥接在线')
    : t('platforms.wechatLocal.offline', '待连接');
  const apiBase = new URL(getApiBaseUrl(), window.location.origin).href.replace(/\/$/, '');
  // PowerShell single-quoted arguments are literal and escape a quote by doubling it.
  const runtime = `.tmp/wechat-local-bridge/channels/${platform.id}`;
  const bridgeCommand = `python -m tools.wechat_local_bridge.cli --runtime ${runtime}`;
  const bindCommand = `${bridgeCommand} bind-tgo --platform-type wechat_local --base-url '${apiBase.replace(/'/g, "''")}' --platform-id ${platform.id} --calibration-runtime .tmp/wechat-local-bridge`;
  const startCommand = `${bridgeCommand} serve --rpa-binding ${runtime}/rpa-binding.private.json --tgo-config ${runtime}/tgo.private.json --test-prefix '个人微信测试：' --allow-send`;

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
        ai_mode: aiMode,
        fallback_to_ai_timeout: aiMode === 'assist' ? timeout : null,
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
          <div>
            <h2 className="font-semibold">{t('platforms.wechatLocal.bridge', 'Windows 本机桥接')}</h2>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              {t('platforms.wechatLocal.scope', '当前仅测试「文件传输助手」的文字收发。桥接程序需在已登录微信的 Windows 电脑上运行。')}
            </p>
            {lastSeen && (
              <p className="mt-1 text-sm text-gray-500">
                {t('platforms.wechatLocal.lastSeen', '最近上报 {{time}}', { time: new Date(lastSeen).toLocaleString() })}
              </p>
            )}
          </div>
          <details className="text-sm" open={connected ? undefined : true}>
            <summary className="cursor-pointer font-medium">{t('platforms.wechatLocal.setup', '连接步骤')}</summary>
            <p className="my-3 text-gray-600 dark:text-gray-300">
              {t('platforms.wechatLocal.setupHint', '在已完成微信读取和窗口校准的项目目录中打开 PowerShell。执行绑定命令，按提示粘贴渠道密钥，然后执行启动命令。用「个人微信测试：」开头的消息进行试发。')}
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="rounded-md border px-3 py-1.5" onClick={() => void copy(bindCommand)}>
                {t('platforms.wechatLocal.copyBind', '复制绑定命令')}
              </button>
              <button type="button" className="rounded-md border px-3 py-1.5" disabled={!platform.config.apiKey}
                onClick={() => void copy(platform.config.apiKey ?? '')}>
                {t('platforms.wechatLocal.copyKey', '复制渠道密钥')}
              </button>
              <button type="button" className="rounded-md border px-3 py-1.5" onClick={() => void copy(startCommand)}>
                {t('platforms.wechatLocal.copyStart', '复制启动命令')}
              </button>
            </div>
          </details>
        </section>
        <section className="space-y-3">
          <PlatformAISettings
            platform={platform}
            agentIds={agentIds}
            aiMode={aiMode}
            fallbackTimeout={timeout}
            onAgentIdsChange={setAgentIds}
            onAIModeChange={setAiMode}
            onFallbackTimeoutChange={setTimeoutSeconds}
          />
          <button type="button" disabled={isSaving} onClick={() => void save()}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {t('platforms.wechatLocal.save', '保存客服设置')}
          </button>
        </section>
      </div>
    </div>
  );
}
