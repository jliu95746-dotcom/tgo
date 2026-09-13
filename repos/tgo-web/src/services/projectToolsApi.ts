/**
 * Project Tools API Service
 * Handles Project Tools API endpoints using NEW /v1/ai/tools API
 */

import { apiClient, APIError } from './api';
import type { AiToolResponse, AiToolCreateRequest, ToolType } from '@/types';
import { redactMcpHeaderValues } from '@/utils/mcpHeaders';

// Helper function to handle API errors consistently
const handleApiError = (error: unknown): string => {
  if (error instanceof APIError) {
    return error.getUserMessage();
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'An unexpected error occurred';
};

// Helper function to check if user is authenticated
export const isAuthenticated = (): boolean => {
  const token = apiClient.getToken();
  return token !== null && token.trim() !== '';
};

// API Endpoints - Use relative paths since the API client already includes the base URL
const API_VERSION = 'v1';

export const PROJECT_TOOLS_ENDPOINTS = {
  AI_TOOLS: `/${API_VERSION}/ai/tools`,
  AI_TOOL_BY_ID: (id: string) => `/${API_VERSION}/ai/tools/${id}`,
} as const;

// Query parameters interface for AI tools list
export interface AiToolsQueryParams {
  tool_type?: ToolType | 'ALL'; // Filter by tool type: "MCP" | "FUNCTION" | "ALL"
  include_deleted?: boolean; // Include soft-deleted tools (default: false)
}

/**
 * Project Tools API Service Class
 * Uses NEW /v1/ai/tools API
 */
export class ProjectToolsApiService {
  /**
   * Get list of AI tools for the current project
   * Returns flat array (no pagination)
   */
  static async getAiTools(params?: AiToolsQueryParams): Promise<AiToolResponse[]> {
    try {
      const queryParams = new URLSearchParams();

      // Add query parameters if provided
      if (params?.tool_type) queryParams.append('tool_type', params.tool_type);
      if (params?.include_deleted !== undefined) {
        queryParams.append('include_deleted', params.include_deleted.toString());
      }

      const url = queryParams.toString()
        ? `${PROJECT_TOOLS_ENDPOINTS.AI_TOOLS}?${queryParams.toString()}`
        : PROJECT_TOOLS_ENDPOINTS.AI_TOOLS;

      return await apiClient.get<AiToolResponse[]>(url);
    } catch (error) {
      console.error('Failed to fetch AI tools:', error);
      throw new Error(handleApiError(error));
    }
  }

  /**
   * Get a specific AI tool by ID
   */
  static async getAiTool(id: string): Promise<AiToolResponse> {
    try {
      return await apiClient.get<AiToolResponse>(
        PROJECT_TOOLS_ENDPOINTS.AI_TOOL_BY_ID(id)
      );
    } catch (error) {
      console.error(`Failed to fetch AI tool ${id}:`, error);
      throw new Error(handleApiError(error));
    }
  }

  /**
   * Get active (non-deleted) AI tools
   */
  static async getActiveAiTools(toolType?: ToolType): Promise<AiToolResponse[]> {
    try {
      const tools = await this.getAiTools({
        tool_type: toolType,
        include_deleted: false,
      });
      // Filter out deleted tools (extra safety)
      return tools.filter(tool => !tool.deleted_at);
    } catch (error) {
      console.error('Failed to fetch active AI tools:', error);
      throw new Error(handleApiError(error));
    }
  }

  /**
   * Get all tools (generic)
   */
  static async getTools(includeDeleted: boolean = false): Promise<AiToolResponse[]> {
    try {
      return await this.getAiTools({
        tool_type: 'ALL',
        include_deleted: includeDeleted,
      });
    } catch (error) {
      console.error('Failed to fetch tools:', error);
      throw new Error(handleApiError(error));
    }
  }

  /**
   * Create a new AI tool for the project
   */
  static async createAiTool(data: AiToolCreateRequest): Promise<AiToolResponse> {
    try {
      return await apiClient.post<AiToolResponse>(
        PROJECT_TOOLS_ENDPOINTS.AI_TOOLS,
        data
      );
    } catch (error) {
      const message = redactMcpHeaderValues(handleApiError(error), data.config?.headers);
      console.error('Failed to create AI tool:', message);
      throw new Error(message);
    }
  }

  /**
   * Update an existing AI tool
   */
  static async updateAiTool(
    toolId: string,
    data: import('@/types').AiToolUpdateRequest
  ): Promise<AiToolResponse> {
    try {
      return await apiClient.patch<AiToolResponse>(
        PROJECT_TOOLS_ENDPOINTS.AI_TOOL_BY_ID(toolId),
        data
      );
    } catch (error) {
      const message = redactMcpHeaderValues(handleApiError(error), data.config?.headers);
      console.error(`Failed to update AI tool ${toolId}:`, message);
      throw new Error(message);
    }
  }

  /**
   * Delete an AI tool (soft delete)
   */
  static async deleteAiTool(id: string): Promise<AiToolResponse> {
    try {
      return await apiClient.delete<AiToolResponse>(
        PROJECT_TOOLS_ENDPOINTS.AI_TOOL_BY_ID(id)
      );
    } catch (error) {
      console.error(`Failed to delete AI tool ${id}:`, error);
      throw new Error(handleApiError(error));
    }
  }
}

// Export default instance for convenience
export default ProjectToolsApiService;
