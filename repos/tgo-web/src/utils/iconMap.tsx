// 图标映射：从lucide图标名称到React组件
import React from 'react';
import {
  // 基础图标
  Plus,
  Search,
  Settings,
  RefreshCw,
  Eye,
  EyeOff,
  Copy,
  Pencil,
  Trash2,
  Share2,
  MoreHorizontal as Ellipsis,
  
  // 导航图标
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  
  // 界面图标
  Smile,
  Scissors,
  Image,
  ListChecks,
  Folder,
  FolderOpen,
  Clock,
  Star,
  Meh,
  MousePointerClick,
  Ticket,
  CirclePlus,
  Inbox,
  
  // 通信图标
  Bot,
  Users,
  Globe,
  Mail,
  Phone,
  Webhook,
  Circle,
  
  // 工具图标
  Wrench,
  Activity,
  
  // 其他
  type LucideIcon
} from 'lucide-react';

// 图标映射对象
export const iconMap: Record<string, LucideIcon> = {
  // 基础操作
  plus: Plus,
  search: Search,
  settings: Settings,
  'refresh-cw': RefreshCw,
  eye: Eye,
  'eye-off': EyeOff,
  copy: Copy,
  pencil: Pencil,
  'trash-2': Trash2,
  'share-2': Share2,
  ellipsis: Ellipsis,
  
  // 导航
  'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight,
  'arrow-down-up': ArrowUpDown,
  
  // 界面元素
  smile: Smile,
  scissors: Scissors,
  image: Image,
  'list-checks': ListChecks,
  folder: Folder,
  'folder-open': FolderOpen,
  clock: Clock,
  star: Star,
  meh: Meh,
  'mouse-pointer-click': MousePointerClick,
  ticket: Ticket,
  'circle-plus': CirclePlus,
  inbox: Inbox,
  
  // 通信和渠道
  bot: Bot,
  users: Users,
  globe: Globe,
  mail: Mail,
  phone: Phone,
  custom: Webhook,
  circle: Circle,

  // 工具
  wrench: Wrench,
  activity: Activity
};

// 获取图标组件的辅助函数
export const getIcon = (iconName: string): LucideIcon | null => {
  return iconMap[iconName] || null;
};

// 图标组件包装器，用于统一处理className和其他属性
interface IconProps {
  name: string;
  className?: string;
  size?: number;
  color?: string;
  [key: string]: any;
}

export const Icon: React.FC<IconProps> = ({ name, className, size, color, ...props }) => {
  const IconComponent = getIcon(name);
  
  if (!IconComponent) {
    console.warn(`Icon "${name}" not found in iconMap`);
    return null;
  }
  
  return (
    <IconComponent 
      className={className}
      size={size}
      color={color}
      {...props}
    />
  );
};

export default Icon;
