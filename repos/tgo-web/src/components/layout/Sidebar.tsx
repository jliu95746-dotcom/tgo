import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LuLibrary, LuMessageCircle, LuRadioTower, LuSettings, LuSparkles, LuUsers } from 'react-icons/lu';
import { NAVIGATION_ITEMS } from '@/utils/constants';
import { useAuthStore } from '@/stores/authStore';
import type { NavigationItem } from '@/types';

interface NavItemProps {
  item: NavigationItem;
}

// Icon mapping for navigation items using react-icons
const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  'MessageCircle': LuMessageCircle,
  'Sparkles': LuSparkles,
  'Library': LuLibrary,
  'Users': LuUsers,
  'Platform': LuRadioTower,
  'Settings': LuSettings
};

/**
 * Navigation item component using React Router NavLink
 */
const NavItem: React.FC<NavItemProps> = ({ item }) => {
  const location = useLocation();
  const { t } = useTranslation();
  const isAIRoute = item.path.startsWith('/ai');
  const IconComponent = ICON_MAP[item.icon];

  return (
    <NavLink
      to={item.path}
      className={({ isActive }) => `
        p-2 rounded-lg transition-colors duration-200 block
        ${isActive || (isAIRoute && location.pathname.startsWith('/ai'))
          ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400'
          : 'text-gray-500 dark:text-gray-400 hover:bg-gray-200/50 dark:hover:bg-gray-700/50 hover:text-gray-700 dark:hover:text-gray-300'
        }
      `}
      title={t(item.title)}
    >
      {IconComponent && <IconComponent className="w-6 h-6" />}
    </NavLink>
  );
};



/**
 * Sidebar component with navigation and logo
 */
const Sidebar: React.FC = () => {
  const user = useAuthStore(state => state.user);
  const isAdmin = user?.role === 'admin';

  // Filter navigation items based on user role
  // Non-admin users cannot see 'channels' (接入平台) and 'ai' (AI 功能)
  const filteredItems = NAVIGATION_ITEMS.filter(item => {
    if (!isAdmin && (item.id === 'channels' || item.id === 'ai')) {
      return false;
    }
    return true;
  });

  return (
    <aside className="w-16 flex flex-col items-center bg-white/70 dark:bg-gray-900/70 backdrop-blur-lg border-r border-gray-200/50 dark:border-gray-700/50 py-4 space-y-4 shrink-0 relative z-20">
      {/* System Logo */}
      <div className="mb-2">
          <img src="/logo.svg" alt="Logo" className="w-8 h-8 object-contain object-center select-none" />
      </div>

      {/* Navigation */}
      <nav className="flex flex-col items-center space-y-3">
        {filteredItems.map((item) => (
          <NavItem key={item.id} item={item} />
        ))}
      </nav>

      {/* Footer spacer */}
      <div className="mt-auto" />
    </aside>
  );
};

export default Sidebar;
