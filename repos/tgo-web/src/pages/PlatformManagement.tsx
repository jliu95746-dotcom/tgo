import React, { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import PlatformList from '../components/platforms/PlatformList';
import { usePlatformStore } from '@/stores/platformStore';

/**
 * Platform Management layout component with nested routing
 */
const PlatformManagement: React.FC = () => {
  const { i18n } = useTranslation();
  const initializePlatformStore = usePlatformStore(state => state.initializeStore);

  useEffect(() => {
    // Fetch platform list when the page is displayed or language changes
    // This ensures data is refreshed after language change or navigation
    initializePlatformStore();
  }, [initializePlatformStore, i18n.language]);

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

