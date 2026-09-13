import { apiClient } from './api';

export type ToolJson = null | boolean | number | string | ToolJson[] | { [key: string]: ToolJson };
export interface MCPConnection {
  endpoint: string;
  transport: 'http' | 'sse';
  headers: Record<string, string>;
}
export interface DiscoveredTool {
  name: string;
  description: string;
  input_schema: Record<string, ToolJson>;
}
export interface DiscoveryResult { success: boolean; tools: DiscoveredTool[]; error?: string | null }
export interface ToolTestResult { success: boolean; output_data?: ToolJson; error?: string | null }

export const toolProbeApi = {
  discover: (connection: MCPConnection) => apiClient.post<DiscoveryResult>('/v1/ai/tools/discover', connection),
  execute: (toolId: string, input: Record<string, ToolJson>) => apiClient.post<ToolTestResult>(`/v1/ai/tools/${encodeURIComponent(toolId)}/execute`, { input_data: input }),
};
