import React from 'react';
import { Wrench, ExternalLink } from 'lucide-react';
import type { AgentToolResponse } from '@/types';

interface AgentToolTagProps {
  tool: AgentToolResponse;
  onClick?: (tool: AgentToolResponse) => void;
  size?: 'xs' | 'sm' | 'md';
  showIcon?: boolean;
  className?: string;
}

/**
 * Agent工具标签组件
 * 用于显示AI员工关联的工具（基于API响应的工具对象）
 */
const AgentToolTag: React.FC<AgentToolTagProps> = ({
  tool,
  onClick,
  size = 'sm',
  showIcon = true,
  className = ''
}) => {
  // 根据工具启用状态确定颜色主题
  const getColorTheme = (enabled: boolean) => {
    if (enabled) {
      return 'bg-green-100 text-green-800 border-green-200 hover:bg-green-200';
    } else {
      return 'bg-gray-100 text-gray-600 border-gray-200 hover:bg-gray-200';
    }
  };

  // 根据工具启用状态确定图标颜色
  const getIconColor = (enabled: boolean) => {
    if (enabled) {
      return 'text-green-600';
    } else {
      return 'text-gray-400';
    }
  };

  // 格式化工具名称显示（容错 undefined）
  const formatToolName = (toolName?: string) => {
    const safe = typeof toolName === 'string' ? toolName : '';
    if (!safe) {
      return { provider: '', name: '', displayName: '工具' };
    }
    // 如果工具名称包含冒号，显示冒号后的部分作为主要名称
    if (safe.includes(':')) {
      const parts = safe.split(':');
      const provider = parts[0];
      const name = parts.slice(1).join(':');
      return { provider, name, displayName: name };
    }
    return { provider: '', name: safe, displayName: safe };
  };

  const sizeClasses = {
    xs: 'px-1.5 py-0.5 text-[11px]',
    sm: 'px-2 py-1 text-xs',
    md: 'px-3 py-1.5 text-sm'
  };

  const iconSizeClasses = {
    xs: 'w-3 h-3',
    sm: 'w-3 h-3',
    md: 'w-4 h-4'
  };

  const colorTheme = getColorTheme(tool.enabled);
  const iconColor = getIconColor(tool.enabled);
  const { provider, displayName } = formatToolName(tool?.tool_name);
  // Prefer API title/name over generic fallback label when tool_name is missing
  const titleFromApi = (tool as any)?.title || (tool as any)?.name;
  const baseFromToolName = displayName && displayName !== '工具' ? displayName : undefined;
  const finalDisplayName = titleFromApi || baseFromToolName || (tool as any)?.id || '工具';
  const providerDisplay = (tool as any)?.tool_server?.short_no || (tool as any)?.tool_source_type || provider;

  const handleClick = () => {
    if (onClick) {
      onClick(tool);
    }
  };

  return (
    <button
      type="button"
      disabled={!onClick}
      className={`
        inline-flex items-center rounded-md border transition-colors duration-200
        ${sizeClasses[size]}
        ${colorTheme}
        ${onClick ? 'cursor-pointer' : 'cursor-default'}
        ${className}
      `}
      onClick={handleClick}
      title={`${tool?.tool_name || '工具'}${tool?.enabled ? ' (已启用)' : ' (已禁用)'}`}
    >
      {showIcon && (
        <Wrench className={`${iconSizeClasses[size]} mr-1 ${iconColor}`} />
      )}
      <span className="font-medium truncate max-w-[9rem]">
        {finalDisplayName}
      </span>
      {providerDisplay && size !== 'xs' && (
        <span className="ml-1 text-[10px] opacity-60">
          ({providerDisplay})
        </span>
      )}
      {onClick && (
        <ExternalLink className={`${iconSizeClasses[size]} ml-1 opacity-60`} />
      )}
    </button>
  );
};

export default AgentToolTag;
