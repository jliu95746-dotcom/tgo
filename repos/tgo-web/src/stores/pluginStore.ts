/**
 * Plugin State Management Store
 */

import { create } from 'zustand';
import i18next from 'i18next';
import yaml from 'js-yaml';
import { pluginApiService } from '@/services/pluginApi';
import type {
  InstalledPluginInfo,
  PluginPanelItem,
  ChatToolbarButton,
  PluginRenderResponse,
  PluginActionResponse,
} from '@/types/plugin';

interface PluginState {
  // Data
  installedPlugins: InstalledPluginInfo[];
  visitorPanels: Record<string, PluginPanelItem[]>; // visitorId -> panels
  toolbarButtons: ChatToolbarButton[];
  
  // UI State
  isLoadingInstalled: boolean;
  installedPluginsError: string | null;
  isLoadingVisitorPanels: boolean;
  isLoadingToolbarButtons: boolean;
  activeModal: {
    pluginId: string;
    title: string;
    ui: PluginRenderResponse;
    context: any;
  } | null;

  // Actions
  fetchInstalledPlugins: () => Promise<void>;
  fetchVisitorPanels: (visitorId: string, context?: any) => Promise<void>;
  fetchToolbarButtons: () => Promise<void>;
  fetchPluginInfo: (url: string) => Promise<any>;
  checkPluginUpdate: (pluginId: string) => Promise<any>;
  upgradePluginStream: (
    pluginId: string,
    latestConfig: any,
    onProgress: (data: { stage: string; message: string }) => void,
    onError: (error: any) => void
  ) => Promise<void>;
  
  // Installation & Lifecycle Actions
  installPlugin: (config: string | any) => Promise<void>;
  installPluginStream: (
    config: any,
    onProgress: (data: { stage: string; message: string }) => void,
    onError: (error: any) => void
  ) => Promise<void>;
  uninstallPlugin: (pluginId: string) => Promise<void>;
  startPlugin: (pluginId: string) => Promise<void>;
  stopPlugin: (pluginId: string) => Promise<void>;
  restartPlugin: (pluginId: string) => Promise<void>;
  getPluginLogs: (pluginId: string) => Promise<string[]>;
  generateDevToken: (projectId: string) => Promise<{ token: string; expires_at: string }>;
  
  // Modal & Event Actions
  openPluginModal: (pluginId: string, title: string, context: any) => Promise<void>;
  closePluginModal: () => void;
  sendPluginEvent: (pluginId: string, eventType: string, actionId: string, context: any, formData?: any) => Promise<void>;
  
  // Action Handling
  handlePluginAction: (pluginId: string, action: PluginActionResponse, context: any) => void;
}

export const usePluginStore = create<PluginState>((set, get) => ({
  installedPlugins: [],
  visitorPanels: {},
  toolbarButtons: [],
  isLoadingInstalled: false,
  installedPluginsError: null,
  isLoadingVisitorPanels: false,
  isLoadingToolbarButtons: false,
  activeModal: null,

  fetchInstalledPlugins: async () => {
    set({ isLoadingInstalled: true, installedPluginsError: null });
    try {
      const { plugins } = await pluginApiService.listInstalledPlugins();
      set({ installedPlugins: plugins });
    } catch (error) {
      console.error('Failed to fetch installed plugins:', error);
      set({ installedPluginsError: error instanceof Error
        ? error.message
        : i18next.t('settings.plugins.loadErrorFallback', '无法加载插件列表，请稍后重试。') });
    } finally {
      set({ isLoadingInstalled: false });
    }
  },

  installPlugin: async (config: string | any) => {
    try {
      const parsedConfig = typeof config === 'string' ? yaml.load(config) : config;
      await pluginApiService.installPlugin(parsedConfig);
      await get().fetchInstalledPlugins();
    } catch (error) {
      console.error('Failed to install plugin:', error);
      throw error;
    }
  },

  installPluginStream: async (config, onProgress, onError) => {
    try {
      const parsedConfig = typeof config === 'string' ? yaml.load(config) : config;
      await pluginApiService.installPluginStream(parsedConfig, onProgress, onError);
    } catch (error) {
      console.error('Failed to start installation stream:', error);
      throw error;
    }
  },

  uninstallPlugin: async (pluginId: string) => {
    try {
      await pluginApiService.uninstallPlugin(pluginId);
      await get().fetchInstalledPlugins();
    } catch (error) {
      console.error('Failed to uninstall plugin:', error);
      throw error;
    }
  },

  startPlugin: async (pluginId: string) => {
    try {
      await pluginApiService.startPlugin(pluginId);
      await get().fetchInstalledPlugins();
    } catch (error) {
      console.error('Failed to start plugin:', error);
      throw error;
    }
  },

  stopPlugin: async (pluginId: string) => {
    try {
      await pluginApiService.stopPlugin(pluginId);
      await get().fetchInstalledPlugins();
    } catch (error) {
      console.error('Failed to stop plugin:', error);
      throw error;
    }
  },

  restartPlugin: async (pluginId: string) => {
    try {
      await pluginApiService.restartPlugin(pluginId);
      await get().fetchInstalledPlugins();
    } catch (error) {
      console.error('Failed to restart plugin:', error);
      throw error;
    }
  },

  getPluginLogs: async (pluginId: string) => {
    try {
      const { logs } = await pluginApiService.getPluginLogs(pluginId);
      return logs;
    } catch (error) {
      console.error('Failed to fetch plugin logs:', error);
      return [];
    }
  },

  generateDevToken: async (projectId: string) => {
    try {
      return await pluginApiService.generateDevToken(projectId);
    } catch (error) {
      console.error('Failed to generate dev token:', error);
      throw error;
    }
  },

  fetchVisitorPanels: async (visitorId, context) => {
    set({ isLoadingVisitorPanels: true });
    try {
      const { panels } = await pluginApiService.renderVisitorPanels({
        visitor_id: visitorId,
        context,
        language: i18next.language,
      });
      set((state) => ({
        visitorPanels: {
          ...state.visitorPanels,
          [visitorId]: panels,
        },
      }));
    } catch (error) {
      console.error(`Failed to fetch visitor panels for ${visitorId}:`, error);
    } finally {
      set({ isLoadingVisitorPanels: false });
    }
  },

  fetchToolbarButtons: async () => {
    set({ isLoadingToolbarButtons: true });
    try {
      const { buttons } = await pluginApiService.getChatToolbarButtons();
      set({ toolbarButtons: buttons });
    } catch (error) {
      console.error('Failed to fetch toolbar buttons:', error);
    } finally {
      set({ isLoadingToolbarButtons: false });
    }
  },

  fetchPluginInfo: async (url: string) => {
    try {
      return await pluginApiService.fetchPluginInfo(url);
    } catch (error) {
      console.error('Failed to fetch plugin info:', error);
      throw error;
    }
  },

  checkPluginUpdate: async (pluginId: string) => {
    try {
      return await pluginApiService.checkPluginUpdate(pluginId);
    } catch (error) {
      console.error('Failed to check plugin update:', error);
      throw error;
    }
  },

  upgradePluginStream: async (pluginId, latestConfig, onProgress, onError) => {
    try {
      await pluginApiService.upgradePluginStream(pluginId, latestConfig, onProgress, onError);
      await get().fetchInstalledPlugins();
    } catch (error) {
      console.error('Failed to start upgrade stream:', error);
      throw error;
    }
  },

  openPluginModal: async (pluginId, title, context) => {
    try {
      const ui = await pluginApiService.renderChatToolbarPlugin(pluginId, {
        ...context,
        language: i18next.language,
      });
      set({
        activeModal: {
          pluginId,
          title,
          ui,
          context,
        },
      });
    } catch (error) {
      console.error(`Failed to open plugin modal for ${pluginId}:`, error);
    }
  },

  closePluginModal: () => {
    set({ activeModal: null });
  },

  sendPluginEvent: async (pluginId, eventType, actionId, context, formData) => {
    try {
      const action = await pluginApiService.sendPluginEvent(pluginId, {
        event_type: eventType,
        action_id: actionId,
        extension_type: context.extension_type,
        visitor_id: context.visitor_id,
        session_id: context.session_id,
        language: i18next.language,
        form_data: formData,
        payload: context,
      });
      get().handlePluginAction(pluginId, action, context);
    } catch (error) {
      console.error(`Failed to send plugin event to ${pluginId}:`, error);
    }
  },

  handlePluginAction: (pluginId, action, context) => {
    const { action: type, data } = action;
    console.log(`Handling plugin action from ${pluginId}: ${type}`, data);

    if (type === 'batch' && data?.actions && Array.isArray(data.actions)) {
      for (const subAction of data.actions) {
        get().handlePluginAction(pluginId, subAction, context);
      }
      return;
    }

    switch (type) {
      case 'open_url':
        if (data.url) {
          window.open(data.url, data.target || '_blank');
        }
        break;
      
      case 'insert_text':
        if (data.text) {
          window.dispatchEvent(new CustomEvent('tgo:insert_text', { detail: { text: data.text, replace: data.replace } }));
        }
        break;

      case 'send_message':
        if (data.content) {
          window.dispatchEvent(new CustomEvent('tgo:send_message', { detail: { content: data.content, content_type: data.content_type } }));
        }
        break;

      case 'show_toast':
        if (data.message) {
          window.dispatchEvent(new CustomEvent('tgo:show_toast', { detail: { message: data.message, type: data.type || 'success' } }));
        }
        break;

      case 'copy_text':
        if (data.text) {
          navigator.clipboard.writeText(data.text);
        }
        break;

      case 'refresh':
        if (get().activeModal?.pluginId) {
          get().openPluginModal(get().activeModal!.pluginId, get().activeModal!.title, context);
        }
        if (context.visitor_id) {
          get().fetchVisitorPanels(context.visitor_id, context);
        }
        break;

      case 'close_modal':
        get().closePluginModal();
        break;

      case 'show_modal':
        if (data.template && data.data) {
          set({
            activeModal: {
              pluginId,
              title: data.title || 'Plugin',
              ui: { template: data.template, data: data.data },
              context,
            },
          });
        }
        break;

      case 'noop':
      default:
        break;
    }
  },
}));
