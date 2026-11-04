import React from 'react';
import { NavLink } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LuBot, LuWrench, LuGitBranch, LuSparkles,LuMonitorSmartphone, LuZap } from 'react-icons/lu';
import { AI_MENU_ITEMS } from '@/utils/constants';
import type { NavigationItem } from '@/types';
import OnboardingSidebarPanel from '@/components/onboarding/OnboardingSidebarPanel';

interface AIMenuItemProps {
  item: NavigationItem;
}

// Icon mapping for AI menu items using react-icons
const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  Bot: LuBot,
  Wrench: LuWrench,
  Zap: LuZap,
  GitBranch: LuGitBranch,
  Monitor: LuMonitorSmartphone
};

/**
 * AI menu navigation item component using React Router NavLink
 */
const AIMenuItem: React.FC<AIMenuItemProps> = ({ item }) => {
  const { t } = useTranslation();
  const IconComponent = ICON_MAP[item.icon];

  return (
    <NavLink
      to={item.path}
      className={({ isActive }) => `
        group mb-1 flex w-auto shrink-0 items-center rounded-2xl px-3 py-2 text-left text-sm transition-all duration-300 md:w-full md:px-4 md:py-3
        ${isActive
          ? 'bg-white dark:bg-gray-800 text-blue-600 dark:text-blue-400 font-bold shadow-sm border border-gray-100 dark:border-gray-700'
          : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/50 dark:hover:bg-gray-800/50 hover:text-gray-900 dark:hover:text-gray-200 border border-transparent'
        }
      `}
    >
      {/* Icon Container - using render prop pattern for isActive */}
      {({ isActive }) => (
        <>
          <div className={`
            mr-1 rounded-xl p-1 transition-all duration-300 md:mr-3 md:p-2
            ${isActive 
              ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 shadow-inner' 
              : 'bg-transparent text-gray-400 group-hover:bg-gray-100 dark:group-hover:bg-gray-700 group-hover:text-gray-600'
            }
          `}>
            {IconComponent && <IconComponent className="w-4.5 h-4.5" />}
          </div>
          <span className="flex-1 truncate">{t(item.title)}</span>
          
          {/* Active Indicator Dot */}
          <div className={`
            w-1.5 h-1.5 rounded-full bg-blue-500 shadow-sm transition-all duration-300
            ${isActive ? 'opacity-100 scale-100' : 'opacity-0 scale-0'}
          `} />
        </>
      )}
    </NavLink>
  );
};

/**
 * AI feature menu component
 */
const AIMenu: React.FC = () => {
  const { t } = useTranslation();
  return (
    <aside className="ai-section-menu flex w-full min-w-0 shrink-0 flex-col border-b border-gray-200/50 bg-[#f8fafc] dark:border-gray-800/50 dark:bg-gray-950 md:w-64 md:border-r md:border-b-0">
      {/* Header */}
      <div className="hidden px-6 py-8 md:block">
        <div className="flex items-center gap-3 mb-1">
          <div className="p-2 bg-blue-600 rounded-xl text-white shadow-lg shadow-blue-200 dark:shadow-none">
            <LuSparkles className="w-5 h-5" />
          </div>
          <h3 className="text-xl font-bold text-gray-900 dark:text-gray-100 tracking-tight">
            {t('ai.menu.title', 'AI 功能')}
          </h3>
        </div>
        <p className="text-[11px] text-gray-400 font-bold uppercase tracking-[0.2em] pl-11">
          Features
        </p>
      </div>

      {/* Menu Navigation */}
      <nav aria-label={t('ai.menu.title', 'AI 功能')} className="custom-scrollbar flex min-w-0 gap-1 overflow-x-auto px-2 py-2 md:block md:flex-grow md:space-y-1 md:overflow-x-hidden md:overflow-y-auto md:px-4 md:py-0">
        {AI_MENU_ITEMS.map((item) => (
          <AIMenuItem
            key={item.id}
            item={item}
          />
        ))}
      </nav>

      {/* Onboarding Panel */}
      <div className="mt-auto hidden p-4 md:block">
        <OnboardingSidebarPanel />
      </div>
    </aside>
  );
};

export default AIMenu;
