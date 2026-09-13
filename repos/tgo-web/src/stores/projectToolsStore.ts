/**
 * Project Tools Store
 * Zustand store for managing project tools state
 * Updated to use NEW /v1/ai/tools API
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { AiToolResponse, AiToolCreateRequest, AiToolUpdateRequest, ToolType } from '@/types';
import { ProjectToolsApiService, type AiToolsQueryParams } from '@/services/projectToolsApi';

// Store state interface
interface ProjectToolsState {
  // Data
  aiTools: AiToolResponse[];
  selectedAiTool: AiToolResponse | null;

  // Loading states
  isLoading: boolean;
  isLoadingTool: boolean;
  isCreating: boolean;
  isDeleting: boolean;

  // Error states
  error: string | null;
  toolError: string | null;

  // Filters (client-side filtering since new API returns flat array)
  enabledFilter: 'all' | 'enabled' | 'disabled'; // Maps to deleted_at field
  toolTypeFilter: ToolType | 'ALL'; // Filter by Tool or FUNCTION

  // Actions
  setAiTools: (tools: AiToolResponse[]) => void;
  setSelectedAiTool: (tool: AiToolResponse | null) => void;
  setLoading: (loading: boolean) => void;
  setLoadingTool: (loading: boolean) => void;
  setCreating: (creating: boolean) => void;
  setDeleting: (deleting: boolean) => void;
  setError: (error: string | null) => void;
  setToolError: (error: string | null) => void;
  setEnabledFilter: (filter: 'all' | 'enabled' | 'disabled') => void;
  setToolTypeFilter: (filter: ToolType | 'ALL') => void;

  // Async actions
  loadAiTools: (params?: AiToolsQueryParams) => Promise<void>;
  loadAiTool: (id: string) => Promise<void>;
  loadActiveTools: (toolType?: ToolType) => Promise<void>;
  loadTools: (includeDeleted?: boolean) => Promise<void>;
  createTool: (data: AiToolCreateRequest) => Promise<void>;
  updateTool: (id: string, updateData: AiToolUpdateRequest) => Promise<AiToolResponse>;
  deleteTool: (id: string) => Promise<void>;
  refreshTools: () => Promise<void>;
  clearError: () => void;
  clearToolError: () => void;
  reset: () => void;
}

// Initial state
const initialState = {
  aiTools: [],
  selectedAiTool: null,
  isLoading: false,
  isLoadingTool: false,
  isCreating: false,
  isDeleting: false,
  error: null,
  toolError: null,
  enabledFilter: 'all' as const,
  toolTypeFilter: 'ALL' as const,
};

// Create the store
export const useProjectToolsStore = create<ProjectToolsState>()(
  devtools(
    (set, get) => ({
      ...initialState,

      // Synchronous actions
      setAiTools: (tools) => set({ aiTools: tools }, false, 'setAiTools'),
      setSelectedAiTool: (tool) => set({ selectedAiTool: tool }, false, 'setSelectedAiTool'),
      setLoading: (loading) => set({ isLoading: loading }, false, 'setLoading'),
      setLoadingTool: (loading) => set({ isLoadingTool: loading }, false, 'setLoadingTool'),
      setCreating: (creating) => set({ isCreating: creating }, false, 'setCreating'),
      setDeleting: (deleting) => set({ isDeleting: deleting }, false, 'setDeleting'),
      setError: (error) => set({ error }, false, 'setError'),
      setToolError: (error) => set({ toolError: error }, false, 'setToolError'),
      setEnabledFilter: (filter) => set({ enabledFilter: filter }, false, 'setEnabledFilter'),
      setToolTypeFilter: (filter) => set({ toolTypeFilter: filter }, false, 'setToolTypeFilter'),

      // Async actions
      loadAiTools: async (params) => {
        const { setLoading, setError, setAiTools } = get();

        setLoading(true);
        setError(null);

        try {
          const tools = await ProjectToolsApiService.getAiTools(params);
          setAiTools(tools);
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to load AI tools';
          setError(errorMessage);
          console.error('Failed to load AI tools:', error);
        } finally {
          setLoading(false);
        }
      },

      loadAiTool: async (id) => {
        const { setLoadingTool, setToolError, setSelectedAiTool } = get();

        setLoadingTool(true);
        setToolError(null);

        try {
          const tool = await ProjectToolsApiService.getAiTool(id);
          setSelectedAiTool(tool);
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to load AI tool';
          setToolError(errorMessage);
          console.error(`Failed to load AI tool ${id}:`, error);
        } finally {
          setLoadingTool(false);
        }
      },

      loadActiveTools: async (toolType) => {
        const { setLoading, setError, setAiTools, setEnabledFilter } = get();

        setLoading(true);
        setError(null);
        setEnabledFilter('enabled');

        try {
          const tools = await ProjectToolsApiService.getActiveAiTools(toolType);
          setAiTools(tools);
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to load active tools';
          setError(errorMessage);
          console.error('Failed to load active AI tools:', error);
        } finally {
          setLoading(false);
        }
      },

      loadTools: async (includeDeleted = false) => {
        const { setLoading, setError, setAiTools, setToolTypeFilter } = get();

        setLoading(true);
        setError(null);
        setToolTypeFilter('ALL');

        try {
          const tools = await ProjectToolsApiService.getTools(includeDeleted);
          setAiTools(tools);
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to load Tool tools';
          setError(errorMessage);
          console.error('Failed to load Tool tools:', error);
        } finally {
          setLoading(false);
        }
      },

      createTool: async (data) => {
        const { setCreating, setError, refreshTools } = get();

        setCreating(true);
        setError(null);

        try {
          await ProjectToolsApiService.createAiTool(data);
          await refreshTools(); // Refresh the list after creation
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to create tool';
          setError(errorMessage);
          console.error('Failed to create AI tool:', error);
          throw error; // Re-throw for component handling
        } finally {
          setCreating(false);
        }
      },

      updateTool: async (id, updateData) => {
        const { setError, setAiTools } = get();

        setError(null);

        try {
          const updatedTool = await ProjectToolsApiService.updateAiTool(id, updateData);

          // Update the tool in local state
          setAiTools(
            get().aiTools.map(tool =>
              tool.id === id ? updatedTool : tool
            )
          );

          return updatedTool;
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to update tool';
          setError(errorMessage);
          console.error(`Failed to update AI tool ${id}:`, error);
          throw error; // Re-throw for component handling
        }
      },

      deleteTool: async (id) => {
        const { setDeleting, setError, setAiTools } = get();

        setDeleting(true);
        setError(null);

        try {
          const deletedTool = await ProjectToolsApiService.deleteAiTool(id);

          // Update the tool in local state with deleted_at timestamp
          setAiTools(
            get().aiTools.map(tool =>
              tool.id === id ? deletedTool : tool
            )
          );
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Failed to delete tool';
          setError(errorMessage);
          console.error(`Failed to delete AI tool ${id}:`, error);
          throw error; // Re-throw for component handling
        } finally {
          setDeleting(false);
        }
      },

      refreshTools: async () => {
        const { loadAiTools, enabledFilter, toolTypeFilter } = get();

        // If toolTypeFilter is 'ALL', we use loadTools logic to fetch everything
        if (toolTypeFilter === 'ALL') {
          const includeDeleted = enabledFilter === 'all' || enabledFilter === 'disabled';
          await get().loadTools(includeDeleted);
          return;
        }

        const params: AiToolsQueryParams = {};

        // Map toolTypeFilter to tool_type param
        if (toolTypeFilter !== 'ALL' as ToolType) {
          params.tool_type = toolTypeFilter;
        }

        // Map enabledFilter to include_deleted param
        // 'all' -> include_deleted: true
        // 'enabled' -> include_deleted: false (only active tools)
        // 'disabled' -> include_deleted: true (then filter client-side)
        if (enabledFilter === 'all' || enabledFilter === 'disabled') {
          params.include_deleted = true;
        } else {
          params.include_deleted = false;
        }

        await loadAiTools(params);
      },

      clearError: () => set({ error: null }, false, 'clearError'),
      clearToolError: () => set({ toolError: null }, false, 'clearToolError'),

      reset: () => set(initialState, false, 'reset'),
    }),
    {
      name: 'project-tools-store',
    }
  )
);

export default useProjectToolsStore;
