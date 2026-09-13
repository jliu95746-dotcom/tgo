import React, { useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import i18n from '@/i18n';


// Icon color mapping for semantic visual distinction
const ICON_COLOR_MAP = {
  // Blue family - Documentation and books
  Book: 'text-blue-600',
  BookOpen: 'text-blue-500',
  FileText: 'text-blue-700',
  Library: 'text-blue-600',
  Bookmark: 'text-blue-500',

  // Green family - Data and storage
  Database: 'text-green-600',
  HardDrive: 'text-green-700',
  Archive: 'text-green-500',
  Package: 'text-green-600',
  FolderOpen: 'text-green-500',

  // Purple family - Intelligence and learning
  Brain: 'text-purple-600',
  GraduationCap: 'text-purple-500',
  Lightbulb: 'text-purple-700',
  Target: 'text-purple-600',

  // Orange family - Business and work
  Briefcase: 'text-orange-600',
  Layers: 'text-orange-500',

  // Yellow family - Energy and highlights
  Zap: 'text-yellow-600',
  Star: 'text-yellow-500',

  // Red family - Important and favorites
  Heart: 'text-red-500',

  // Teal family - Global and connectivity
  Globe: 'text-teal-600',
} as const;

// Common knowledge base icons with color mapping
const KNOWLEDGE_BASE_ICONS = [
  { name: 'Book', icon: LucideIcons.Book, title: i18n.t('knowledge.icons.book', '书籍'), color: ICON_COLOR_MAP.Book },
  { name: 'FileText', icon: LucideIcons.FileText, title: i18n.t('knowledge.icons.fileText', '文档'), color: ICON_COLOR_MAP.FileText },
  { name: 'Database', icon: LucideIcons.Database, title: i18n.t('knowledge.icons.database', '数据库'), color: ICON_COLOR_MAP.Database },
  { name: 'Brain', icon: LucideIcons.Brain, title: i18n.t('knowledge.icons.brain', '智能'), color: ICON_COLOR_MAP.Brain },
  { name: 'Lightbulb', icon: LucideIcons.Lightbulb, title: i18n.t('knowledge.icons.lightbulb', '创意'), color: ICON_COLOR_MAP.Lightbulb },
  { name: 'BookOpen', icon: LucideIcons.BookOpen, title: i18n.t('knowledge.icons.bookOpen', '开放书籍'), color: ICON_COLOR_MAP.BookOpen },
  { name: 'Library', icon: LucideIcons.Library, title: i18n.t('knowledge.icons.library', '图书馆'), color: ICON_COLOR_MAP.Library },
  { name: 'GraduationCap', icon: LucideIcons.GraduationCap, title: i18n.t('knowledge.icons.graduationCap', '学习'), color: ICON_COLOR_MAP.GraduationCap },
  { name: 'Bookmark', icon: LucideIcons.Bookmark, title: i18n.t('knowledge.icons.bookmark', '书签'), color: ICON_COLOR_MAP.Bookmark },
  { name: 'Archive', icon: LucideIcons.Archive, title: i18n.t('knowledge.icons.archive', '归档'), color: ICON_COLOR_MAP.Archive },
  { name: 'FolderOpen', icon: LucideIcons.FolderOpen, title: i18n.t('knowledge.icons.folderOpen', '文件夹'), color: ICON_COLOR_MAP.FolderOpen },
  { name: 'HardDrive', icon: LucideIcons.HardDrive, title: i18n.t('knowledge.icons.hardDrive', '存储'), color: ICON_COLOR_MAP.HardDrive },
  { name: 'Layers', icon: LucideIcons.Layers, title: i18n.t('knowledge.icons.layers', '分层'), color: ICON_COLOR_MAP.Layers },
  { name: 'Package', icon: LucideIcons.Package, title: i18n.t('knowledge.icons.package', '包'), color: ICON_COLOR_MAP.Package },
  { name: 'Briefcase', icon: LucideIcons.Briefcase, title: i18n.t('knowledge.icons.briefcase', '公文包'), color: ICON_COLOR_MAP.Briefcase },
  { name: 'Target', icon: LucideIcons.Target, title: i18n.t('knowledge.icons.target', '目标'), color: ICON_COLOR_MAP.Target },
  { name: 'Zap', icon: LucideIcons.Zap, title: i18n.t('knowledge.icons.zap', '闪电'), color: ICON_COLOR_MAP.Zap },
  { name: 'Star', icon: LucideIcons.Star, title: i18n.t('knowledge.icons.star', '星星'), color: ICON_COLOR_MAP.Star },
  { name: 'Heart', icon: LucideIcons.Heart, title: i18n.t('knowledge.icons.heart', '心形'), color: ICON_COLOR_MAP.Heart },
  { name: 'Globe', icon: LucideIcons.Globe, title: i18n.t('knowledge.icons.globe', '全球'), color: ICON_COLOR_MAP.Globe },
] as const;

const DEFAULT_ICON = 'Book';

interface IconPickerProps {
  selectedIcon?: string;
  onIconSelect: (iconName: string) => void;
  disabled?: boolean;
}

export const IconPicker: React.FC<IconPickerProps> = ({
  selectedIcon = DEFAULT_ICON,
  onIconSelect,
  disabled = false
}) => {
  const [isOpen, setIsOpen] = useState(false);

  const selectedIconData = KNOWLEDGE_BASE_ICONS.find(icon => icon.name === selectedIcon) || KNOWLEDGE_BASE_ICONS[0];
  const SelectedIconComponent = selectedIconData.icon;

  const handleIconSelect = (iconName: string) => {
    onIconSelect(iconName);
    setIsOpen(false);
  };

  const handleToggle = () => {
    if (!disabled) {
      setIsOpen(!isOpen);
    }
  };

  return (
    <div className="relative">
      {/* Selected Icon Display */}
      <button
        type="button"
        onClick={handleToggle}
        disabled={disabled}
        className={`w-full flex items-center justify-between px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors ${
          disabled
            ? 'bg-gray-100 dark:bg-gray-800 cursor-not-allowed border-gray-300 dark:border-gray-600'
            : 'bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600 border-gray-300 dark:border-gray-600'
        }`}
        title={selectedIconData.title}
      >
        <div className="flex items-center justify-center">
          <SelectedIconComponent className={`w-5 h-5 ${selectedIconData.color}`} />
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-400 dark:text-gray-500 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-10"
            onClick={() => setIsOpen(false)}
          />

          {/* Icon Grid */}
          <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-md shadow-lg z-20 max-h-64 overflow-y-auto">
            <div className="grid grid-cols-6 gap-1 p-3">
              {KNOWLEDGE_BASE_ICONS.map((iconData) => {
                const IconComponent = iconData.icon;
                const isSelected = iconData.name === selectedIcon;

                return (
                  <button
                    key={iconData.name}
                    type="button"
                    onClick={() => handleIconSelect(iconData.name)}
                    className={`relative flex items-center justify-center p-3 rounded-md transition-colors hover:bg-gray-100 dark:hover:bg-gray-700 ${
                      isSelected ? 'bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700' : 'border border-transparent'
                    }`}
                    title={iconData.title}
                  >
                    <IconComponent className={`w-5 h-5 ${isSelected ? 'text-blue-600 dark:text-blue-400' : iconData.color}`} />
                    {isSelected && (
                      <Check className="absolute -top-1 -right-1 w-3 h-3 text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-800 rounded-full border border-blue-200 dark:border-blue-700" />
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// Export the default icon for use in other components
export { DEFAULT_ICON };

// Export the icon data and color mapping for use in other components
export { KNOWLEDGE_BASE_ICONS, ICON_COLOR_MAP };

// Helper function to get icon component by name
export const getIconComponent = (iconName?: string) => {
  const iconData = KNOWLEDGE_BASE_ICONS.find(icon => icon.name === iconName);
  return iconData ? iconData.icon : KNOWLEDGE_BASE_ICONS[0].icon;
};

// Helper function to get icon color by name
export const getIconColor = (iconName?: string) => {
  const iconData = KNOWLEDGE_BASE_ICONS.find(icon => icon.name === iconName);
  return iconData ? iconData.color : KNOWLEDGE_BASE_ICONS[0].color;
};

// Helper function to get icon title by name (for accessibility)
export const getIconTitle = (iconName?: string) => {
  const iconData = KNOWLEDGE_BASE_ICONS.find(icon => icon.name === iconName);
  return iconData ? iconData.title : KNOWLEDGE_BASE_ICONS[0].title;
};

// Helper function to get complete icon data by name
export const getIconData = (iconName?: string) => {
  return KNOWLEDGE_BASE_ICONS.find(icon => icon.name === iconName) || KNOWLEDGE_BASE_ICONS[0];
};
