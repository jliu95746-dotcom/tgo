import ProjectToolsApi from './projectToolsApi';
import { logisticsApi } from './logisticsApi';
import type { AiToolResponse } from '@/types';
import type { LogisticsProviderConfig } from '@/types/logisticsProvider';

export const isLogisticsProviderTool = (tool: AiToolResponse): boolean =>
  Boolean(tool.config?.logistics_provider) || tool.name === 'express_service';

export async function getLogisticsProviderTool(): Promise<AiToolResponse | null> {
  const [settings, tools] = await Promise.all([logisticsApi.getSettings(), ProjectToolsApi.getTools(false)]);
  return tools.find(tool => tool.id === settings.query_tool_id) || tools.find(isLogisticsProviderTool) || null;
}

export async function saveLogisticsProvider(
  projectId: string, tool: AiToolResponse | null, endpoint: string, provider: LogisticsProviderConfig,
): Promise<{ tool: AiToolResponse; linked: boolean }> {
  // Resolve the project-wide selection before creating anything, even when
  // this entrance was opened before another entrance finished configuring it.
  const settings = await logisticsApi.getSettings();
  const tools = await ProjectToolsApi.getTools(false);
  const target = tools.find(item => item.id === tool?.id)
    || tools.find(item => item.id === settings.query_tool_id)
    || tools.find(isLogisticsProviderTool)
    || null;
  const data = {
    description: `通过 ${provider.provider_name} 查询物流单号的实时轨迹`,
    tool_type: 'FUNCTION' as const,
    transport_type: 'http_webhook', endpoint,
    config: { logistics_provider: provider },
  };
  // Update in place: archive selection and employee bindings retain their IDs.
  const saved = target
    ? await ProjectToolsApi.updateAiTool(target.id, data)
    : await ProjectToolsApi.createAiTool({ ...data, title_zh: '快递查询服务', project_id: projectId, name: `express_service_${crypto.randomUUID().replace(/-/g, '').slice(0, 8)}` });
  try {
    const current = await logisticsApi.getSettings();
    if (current.query_tool_id !== saved.id) {
      // Fetch latest persisted settings so unrelated archive switches are kept.
      await logisticsApi.updateSettings({
        query_tool_id: saved.id, enabled: current.enabled,
        auto_capture_visitor_messages: current.auto_capture_visitor_messages,
        auto_capture_staff_messages: current.auto_capture_staff_messages,
        verify_before_binding: current.verify_before_binding,
        auto_query_on_mention: current.auto_query_on_mention,
        poll_interval_minutes: current.poll_interval_minutes,
        stop_after_delivered: current.stop_after_delivered,
        archive_after_days: current.archive_after_days,
        conflict_policy: current.conflict_policy,
      });
    }
    return { tool: saved, linked: true };
  } catch {
    // Preserve the saved ID on partial failure; retry must not create a copy.
    return { tool: saved, linked: false };
  }
}
