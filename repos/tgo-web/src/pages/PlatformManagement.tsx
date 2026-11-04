import React, { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import PlatformList from '../components/platforms/PlatformList';
import { usePlatformStore } from '@/stores/platformStore';
import { platformsApiService } from '@/services/platformsApi';
import { PlatformType } from '@/types';

/**
 * Platform Management layout component with nested routing
 */
const PlatformManagement: React.FC = () => {
  const { i18n } = useTranslation();
  const initializePlatformStore = usePlatformStore(state => state.initializeStore);
  const setWeChatLocalStatuses = usePlatformStore(state => state.setWeChatLocalStatuses);
  const weChatChannelIds = usePlatformStore(state => state.platforms
    .filter(platform => platform.type === PlatformType.WECHAT_LOCAL
      && platform.status !== 'disabled' && platform.is_supported !== false)
    .map(platform => platform.id).sort().join(','));


  useEffect(() => {
    // Fetch platform list when the page is displayed or language changes
    // This ensures data is refreshed after language change or navigation
    initializePlatformStore();
  }, [initializePlatformStore, i18n.language]);

  useEffect(() => {
    setWeChatLocalStatuses({});
    if (!weChatChannelIds) return;

    const ids = weChatChannelIds.split(',');
    let disposed = false;
    let timer: number;
    const refresh = async () => {
      const statuses = await Promise.all(ids.map(async (id) => {
        try {
          return [id, await platformsApiService.getWeChatLocalStatus(id)] as const;
        } catch {
          return [id, 'error'] as const;
        }
      }));
      if (disposed) return;
      setWeChatLocalStatuses(Object.fromEntries(statuses));
      timer = window.setTimeout(refresh, 5000);
    };
    void refresh();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [weChatChannelIds, setWeChatLocalStatuses]);

  return (
    <div className="platform-workspace flex h-full w-full bg-gray-50 dark:bg-gray-900">
      {/* Platform List Sidebar */}
      <PlatformList />

      {/* Platform Configuration Area - renders child routes */}
      <Outlet />
    </div>
  );
};

export default PlatformManagement;

