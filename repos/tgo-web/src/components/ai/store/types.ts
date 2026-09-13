import type { ModelStoreCategory } from '@/types';

export type StoreType = 'model';

export type StoreCategory = ModelStoreCategory;

export interface StoreModalBaseProps {
  isOpen: boolean;
  onClose: () => void;
  sidebar?: React.ReactNode;
  header?: React.ReactNode;
  children: React.ReactNode;
  themeColor?: 'blue' | 'indigo' | 'purple';
}

export interface StoreSidebarProps {
  title: string;
  icon: React.ReactNode;
  categories: StoreCategory[];
  selectedCategory: string;
  onCategoryChange: (slug: string) => void;
  themeColor?: 'blue' | 'indigo' | 'purple';
}

export interface StoreHeaderProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder: string;
  onClose: () => void;
  themeColor?: 'blue' | 'indigo' | 'purple';
  userStatus?: React.ReactNode;
}

export interface StoreUserStatusProps {
  themeColor?: 'blue' | 'indigo' | 'purple';
  onLoginClick: () => void;
}

export interface StoreContentAreaProps {
  loading: boolean;
  isEmpty: boolean;
  emptyIcon?: React.ReactNode;
  emptyTitle?: string;
  emptyDescription?: string;
  loadingText?: string;
  themeColor?: 'blue' | 'indigo' | 'purple';
  children: React.ReactNode;
}

export interface StoreDetailPanelProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  headerActions?: React.ReactNode;
  footer?: React.ReactNode;
  children: React.ReactNode;
}

export interface StoreCardBaseProps {
  onClick?: () => void;
  featured?: boolean;
  themeColor?: 'blue' | 'indigo' | 'purple';
  className?: string;
  children: React.ReactNode;
}
