import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  PackageSearch,
  RefreshCw,
  Save,
  ShieldCheck,
  Truck,
} from 'lucide-react';
import Toggle from '@/components/ui/Toggle';
import { logisticsApi } from '@/services/logisticsApi';
import ProjectToolsApi from '@/services/projectToolsApi';
import type { AiToolResponse } from '@/types';
import type {
  LogisticsSettings as LogisticsSettingsType,
  LogisticsSettingsUpdate,
} from '@/types/logistics';

const fallbackSettings: LogisticsSettingsUpdate = {
  enabled: true,
  auto_capture_visitor_messages: true,
  auto_capture_staff_messages: true,
  verify_before_binding: true,
  auto_query_on_mention: true,
  query_tool_id: null,
  poll_interval_minutes: 360,
  stop_after_delivered: true,
  archive_after_days: 30,
  conflict_policy: 'manual_review',
};

const toEditableSettings = (
  value: LogisticsSettingsType,
): LogisticsSettingsUpdate => ({
  enabled: value.enabled,
  auto_capture_visitor_messages: value.auto_capture_visitor_messages,
  auto_capture_staff_messages: value.auto_capture_staff_messages,
  verify_before_binding: value.verify_before_binding,
  auto_query_on_mention: value.auto_query_on_mention,
  query_tool_id: value.query_tool_id,
  poll_interval_minutes: value.poll_interval_minutes,
  stop_after_delivered: value.stop_after_delivered,
  archive_after_days: value.archive_after_days,
  conflict_policy: value.conflict_policy,
});

const LogisticsSettings: React.FC = () => {
  const [settings, setSettings] =
    useState<LogisticsSettingsUpdate>(fallbackSettings);
  const [tools, setTools] = useState<AiToolResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testTrackingNo, setTestTrackingNo] = useState('');
  const [notice, setNotice] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);

  useEffect(() => {
    Promise.all([
      logisticsApi.getSettings(),
      ProjectToolsApi.getTools(false),
    ])
      .then(([loadedSettings, loadedTools]) => {
        setSettings(toEditableSettings(loadedSettings));
        setTools(loadedTools);
      })
      .catch((error: unknown) => {
        setNotice({
          type: 'error',
          message:
            error instanceof Error ? error.message : '物流设置加载失败',
        });
      })
      .finally(() => setLoading(false));
  }, []);

  const sortedTools = useMemo(
    () =>
      [...tools].sort((left, right) => {
        const leftText =
          `${left.title_zh || left.title || ''} ${left.name}`.toLowerCase();
        const rightText =
          `${right.title_zh || right.title || ''} ${right.name}`.toLowerCase();
        const isLogistics = (value: string) =>
          /快递|物流|express|shipment|tracking/.test(value);
        return Number(isLogistics(rightText)) - Number(isLogistics(leftText));
      }),
    [tools],
  );

  const update = <Key extends keyof LogisticsSettingsUpdate>(
    key: Key,
    value: LogisticsSettingsUpdate[Key],
  ) => setSettings((current) => ({ ...current, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const saved: LogisticsSettingsType =
        await logisticsApi.updateSettings(settings);
      setSettings(toEditableSettings(saved));
      setNotice({ type: 'success', message: '物流档案设置已保存' });
    } catch (error) {
      setNotice({
        type: 'error',
        message: error instanceof Error ? error.message : '保存失败',
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!testTrackingNo.trim()) {
      setNotice({ type: 'error', message: '请输入用于测试的真实物流单号' });
      return;
    }
    setTesting(true);
    setNotice(null);
    try {
      const result = await logisticsApi.testTool(testTrackingNo.trim());
      setNotice({
        type: 'success',
        message: result.preview
          ? `${result.message}：${result.preview}`
          : result.message,
      });
    } catch (error) {
      setNotice({
        type: 'error',
        message: error instanceof Error ? error.message : '连接测试失败',
      });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin" />
        正在加载物流档案设置…
      </div>
    );
  }

  type BooleanSettingKey =
    | 'enabled'
    | 'auto_capture_visitor_messages'
    | 'auto_capture_staff_messages'
    | 'auto_query_on_mention'
    | 'verify_before_binding'
    | 'stop_after_delivered';
  const switches: Array<{
    key: BooleanSettingKey;
    title: string;
    description: string;
  }> = [
    {
      key: 'enabled',
      title: '启用物流档案',
      description: '开启顾客物流单归档、查询和轨迹展示。',
    },
    {
      key: 'auto_capture_visitor_messages',
      title: '识别顾客发来的物流单号',
      description: '顾客发送单号后自动加入他的档案。',
    },
    {
      key: 'auto_capture_staff_messages',
      title: '识别客服发出的物流单号',
      description: '客服把单号发给顾客时也自动归档。',
    },
    {
      key: 'auto_query_on_mention',
      title: '无单号时使用档案查询',
      description: '顾客只说“查物流”时，优先查询他的进行中物流单。',
    },
    {
      key: 'verify_before_binding',
      title: '顾客单号先验证再确认',
      description: '顾客提供的单号保留待核验状态，降低串单风险。',
    },
    {
      key: 'stop_after_delivered',
      title: '签收后停止自动跟踪',
      description: '已签收物流不再重复刷新，减少查询次数。',
    },
  ];

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="p-2.5 rounded-lg bg-blue-50 dark:bg-blue-900/30">
            <Truck className="w-5 h-5 text-blue-600 dark:text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              物流档案
            </h2>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              管理顾客物流单的自动归档、实时查询与隐私保护。
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-sm font-medium text-white"
        >
          {saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          保存设置
        </button>
      </div>

      {notice && (
        <div
          className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
            notice.type === 'success'
              ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-300'
              : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300'
          }`}
        >
          {notice.type === 'success' ? (
            <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
          ) : (
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          )}
          {notice.message}
        </div>
      )}

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-center gap-2 mb-5">
          <PackageSearch className="w-5 h-5 text-gray-600 dark:text-gray-300" />
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            实时查询工具
          </h3>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-1.5">
            <span className="text-sm text-gray-700 dark:text-gray-300">
              快递查询工具
            </span>
            <select
              value={settings.query_tool_id || ''}
              onChange={(event) =>
                update('query_tool_id', event.target.value || null)
              }
              className="w-full h-10 px-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm text-gray-900 dark:text-gray-100"
            >
              <option value="">未选择</option>
              {sortedTools.map((tool) => (
                <option key={tool.id} value={tool.id}>
                  {tool.title_zh || tool.title || tool.name}
                </option>
              ))}
            </select>
            <span className="block text-xs text-gray-500">
              选择现有“快递查询服务”，档案功能不会重复创建第二个查询工具。
            </span>
          </label>
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 p-4">
            <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
              按需实时查询
            </p>
            <p className="mt-1 text-xs leading-5 text-gray-500">
              客服点击刷新，或顾客提出物流查询时才调用工具；第一阶段不在后台循环请求。
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            value={testTrackingNo}
            onChange={(event) => setTestTrackingNo(event.target.value)}
            placeholder="输入一个物流单号测试连接"
            className="flex-1 h-10 px-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
          />
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !settings.query_tool_id}
            className="inline-flex items-center justify-center gap-2 h-10 px-4 rounded-lg border border-blue-200 dark:border-blue-800 text-sm text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50"
          >
            {testing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            测试查询
          </button>
        </div>
      </section>

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-5">
          自动归档规则
        </h3>
        <div className="divide-y divide-gray-100 dark:divide-gray-700">
          {switches.map((item) => (
            <div
              key={item.key}
              className="flex items-center justify-between gap-5 py-4 first:pt-0 last:pb-0"
            >
              <div>
                <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                  {item.title}
                </p>
                <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                  {item.description}
                </p>
              </div>
              <Toggle
                checked={settings[item.key]}
                onChange={(checked) => update(item.key, checked)}
                aria-label={item.title}
              />
            </div>
          ))}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <label className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5 space-y-2">
          <span className="text-sm font-medium text-gray-800 dark:text-gray-200">
            签收后保留天数
          </span>
          <input
            type="number"
            min={1}
            max={3650}
            value={settings.archive_after_days}
            onChange={(event) =>
              update('archive_after_days', Number(event.target.value))
            }
            className="w-full h-10 px-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
          />
          <span className="block text-xs text-gray-500">
            到期后从进行中列表隐藏，不删除历史记录。
          </span>
        </label>
        <label className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5 space-y-2">
          <span className="text-sm font-medium text-gray-800 dark:text-gray-200">
            单号归属冲突
          </span>
          <select
            value={settings.conflict_policy}
            onChange={(event) =>
              update(
                'conflict_policy',
                event.target.value as LogisticsSettingsUpdate['conflict_policy'],
              )
            }
            className="w-full h-10 px-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
          >
            <option value="manual_review">标记冲突，交给客服核对</option>
            <option value="keep_first">保留第一次绑定</option>
          </select>
          <span className="block text-xs text-gray-500">
            同一单号不会静默绑定到两个顾客，避免泄露物流信息。
          </span>
        </label>
      </section>

      <div className="flex items-start gap-3 rounded-xl border border-blue-100 dark:border-blue-900 bg-blue-50/60 dark:bg-blue-900/10 p-4">
        <ShieldCheck className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0" />
        <p className="text-xs leading-5 text-blue-700 dark:text-blue-300">
          完整物流单号会加密保存；客服界面、日志和接口默认只返回脱敏号码。
        </p>
      </div>
    </div>
  );
};

export default LogisticsSettings;
