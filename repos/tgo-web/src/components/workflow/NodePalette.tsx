/**
 * Node Palette Component
 * Sidebar for dragging new nodes into the workflow canvas
 */

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Search,
  Play,
  Square,
  Bot,
  Wrench,
  GitBranch,
  MessageSquare,
  GitMerge,
  ChevronLeft,
  ChevronRight,
  Globe,
  LayoutGrid,
  Clock,
  Zap,
  ShieldCheck,
} from 'lucide-react';
import { NODE_TYPE_CONFIG, type WorkflowNodeType, type WorkflowCapabilities } from '@/types/workflow';
import { WorkflowApiService } from '@/services/workflowApi';
import { isWorkflowNodeAvailable, isWorkflowNodeConfigurable } from '@/utils/workflowCapabilities';

// Icon mapping
const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Play,
  Square,
  Bot,
  Wrench,
  GitBranch,
  MessageSquare,
  GitMerge,
  Globe,
  LayoutGrid,
  Clock,
  Zap,
  ShieldCheck,
};

interface NodePaletteProps {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

const CATEGORIES = [
  { id: 'trigger', label: 'workflow.categories.trigger', defaultLabel: '触发节点', color: 'bg-green-500', icon: Play },
  { id: 'ai', label: 'workflow.categories.ai', defaultLabel: 'AI 能力', color: 'bg-blue-500', icon: Bot },
  { id: 'logic', label: 'workflow.categories.logic', defaultLabel: '逻辑控制', color: 'bg-purple-500', icon: GitMerge },
  { id: 'external', label: 'workflow.categories.external', defaultLabel: '外部集成', color: 'bg-orange-500', icon: Globe },
  { id: 'output', label: 'workflow.categories.output', defaultLabel: '输出', color: 'bg-indigo-500', icon: MessageSquare },
] as const;

const NodePalette: React.FC<NodePaletteProps> = ({ isCollapsed, onToggleCollapse }) => {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');
  const [capabilities, setCapabilities] = useState<WorkflowCapabilities | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setCapabilities(null); setCapabilitiesError(false);
    WorkflowApiService.getCapabilities().then(data => { if (active) setCapabilities(data); })
      .catch(() => { if (active) setCapabilitiesError(true); });
    return () => { active = false; };
  }, [retry]);

  const nodeTypes: WorkflowNodeType[] = [
    'input',
    'timer',
    'webhook',
    'event',
    'agent',
    'approval',
    'llm',
    'condition',
    'parallel',
    'classifier',
    'tool',
    'api',
    'business_query',
    'answer',
  ];

  const filteredNodeTypes = nodeTypes.filter((type) => {
    const config = NODE_TYPE_CONFIG[type];
    const label = t(`workflow.node_types.${type}.label`, config.label);
    const description = t(`workflow.node_types.${type}.description`, config.description);
    return (
      label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      description.toLowerCase().includes(searchQuery.toLowerCase())
    );
  });

  // Group filtered nodes by category
  const groupedNodes = CATEGORIES.map(cat => ({
    ...cat,
    nodes: filteredNodeTypes.filter(type => NODE_TYPE_CONFIG[type].category === cat.id)
  })).filter(cat => cat.nodes.length > 0);

  const handleDragStart = (event: React.DragEvent, nodeType: WorkflowNodeType) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.setData('text/plain', nodeType); // Fallback
    event.dataTransfer.effectAllowed = 'move';
  };

  const renderNodeItem = (type: WorkflowNodeType) => {
    const config = NODE_TYPE_CONFIG[type];
    const label = t(`workflow.node_types.${type}.label`, config.label);
    const description = t(`workflow.node_types.${type}.description`, config.description);
    const Icon = iconMap[config.icon] || Play;
    const isDisabled = !isWorkflowNodeConfigurable(capabilities, type);
    const preparationOnly = !isDisabled && !isWorkflowNodeAvailable(capabilities, type);
    
    const colorClasses: Record<string, string> = {
      green: 'text-green-600 bg-green-50 dark:bg-green-900/20 border-green-100/50 dark:border-green-800/50',
      red: 'text-red-600 bg-red-50 dark:bg-red-900/20 border-red-100/50 dark:border-red-800/50',
      blue: 'text-blue-600 bg-blue-50 dark:bg-blue-900/20 border-blue-100/50 dark:border-blue-800/50',
      orange: 'text-orange-600 bg-orange-50 dark:bg-orange-900/20 border-orange-100/50 dark:border-orange-800/50',
      purple: 'text-purple-600 bg-purple-50 dark:bg-purple-900/20 border-purple-100/50 dark:border-purple-800/50',
      cyan: 'text-cyan-600 bg-cyan-50 dark:bg-cyan-900/20 border-cyan-100/50 dark:border-cyan-800/50',
      indigo: 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 border-indigo-100/50 dark:border-indigo-800/50',
    };

    return (
      <div
        key={type}
        draggable={!isDisabled}
        onDragStart={(e) => !isDisabled && handleDragStart(e, type)}
        className={`
          group flex items-start gap-3 p-3 rounded-[1.25rem] border transition-all duration-300
          ${isDisabled 
            ? 'opacity-40 cursor-not-allowed grayscale bg-gray-50/50 dark:bg-gray-800/30 border-transparent' 
            : 'bg-white dark:bg-gray-800 border-gray-100 dark:border-gray-700/50 hover:border-purple-300 dark:hover:border-purple-500 hover:bg-white dark:hover:bg-gray-800 hover:shadow-xl hover:shadow-purple-500/10 cursor-grab active:cursor-grabbing active:scale-95'
          }
          ${isCollapsed ? 'justify-center p-2' : ''}
        `}
        title={isCollapsed ? label : ''}
      >
        <div className={`p-2.5 rounded-xl border shrink-0 transition-transform group-hover:scale-110 shadow-sm ${colorClasses[config.color] || colorClasses.blue}`}>
          <Icon className="w-5 h-5" />
        </div>
        
        {!isCollapsed && (
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-bold text-gray-900 dark:text-gray-100 truncate group-hover:text-purple-600 dark:group-hover:text-purple-400 transition-colors">
                {label}
              </div>
              {isDisabled && (
                <span className="shrink-0 text-[8px] bg-gray-100 dark:bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded font-bold uppercase tracking-wider">
                  {capabilities ? t('workflow.palette.underDevelopment', '待开发') : t('businessScenarios.workflow.checkingCapability')}
                </span>
              )}
              {preparationOnly && <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[8px] font-bold text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">{t('workflowWebhook.preparationOnly')}</span>}
            </div>
            <div className="text-[10px] text-gray-400 dark:text-gray-500 line-clamp-1 mt-1 leading-relaxed font-medium opacity-80 group-hover:opacity-100 transition-opacity">
              {description}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      data-collapsed={isCollapsed}
      className={`
        workflow-node-palette
        bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-r border-gray-200/50 dark:border-gray-800/50 
        flex flex-col transition-all duration-300 relative z-20
        ${isCollapsed ? 'w-22' : 'w-80'}
      `}
    >
      {/* Collapse Toggle Button */}
      <button
        onClick={onToggleCollapse}
        className="absolute -right-4 top-8 w-8 h-8 bg-white dark:bg-gray-800 border border-gray-200/50 dark:border-gray-700/50 rounded-full flex items-center justify-center shadow-lg z-10 hover:bg-purple-600 hover:text-white transition-all active:scale-90 group"
      >
        {isCollapsed ? (
          <ChevronRight className="w-4 h-4" />
        ) : (
          <ChevronLeft className="w-4 h-4" />
        )}
      </button>

      {/* Header */}
      {!isCollapsed && (
        <div className="p-8 border-b border-gray-100/50 dark:border-gray-700/50">
          <h3 className="text-[11px] font-bold text-gray-400 dark:text-gray-500 mb-5 uppercase tracking-[0.2em] flex items-center gap-2">
            <LayoutGrid className="w-4 h-4 text-purple-600" />
            {t('workflow.palette.title', '组件库')}
          </h3>
          <div className="relative group">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400 group-focus-within:text-purple-500 transition-colors" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('workflow.palette.search', '搜索组件...')}
              className="w-full pl-11 pr-4 py-3 text-xs bg-gray-100/50 dark:bg-gray-800 border-transparent focus:bg-white dark:focus:bg-gray-800 focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 rounded-2xl dark:text-gray-100 outline-none transition-all font-medium shadow-sm"
            />
          </div>
        </div>
      )}

      {/* Node List with Categories */}
      <div className="flex-1 overflow-y-auto p-6 space-y-10 custom-scrollbar">
        {capabilitiesError && <div role="alert" className="text-xs text-red-600 dark:text-red-400"><p>{t('businessScenarios.workflow.capabilityFailed')}</p><button className="mt-2 underline" onClick={() => setRetry(value => value + 1)}>{t('common.retry', '重试')}</button></div>}
        {groupedNodes.map((cat) => (
          <div key={cat.id} className="space-y-5">
            {!isCollapsed && (
              <div className="flex items-center gap-3 px-1">
                <div className={`p-1.5 rounded-lg ${cat.color} bg-opacity-10 ${cat.color.replace('bg-', 'text-')}`}>
                  {<cat.icon className="w-4 h-4" />}
                </div>
                <h4 className="text-[11px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-widest">
                  {t(cat.label, cat.defaultLabel)}
                </h4>
                <div className="h-px flex-1 bg-gray-100 dark:bg-gray-800 ml-1 opacity-50"></div>
              </div>
            )}
            <div className={`space-y-3 ${isCollapsed ? 'flex flex-col items-center' : ''}`}>
              {cat.nodes.map(type => renderNodeItem(type))}
            </div>
          </div>
        ))}
        
        {groupedNodes.length === 0 && (
          <div className="text-center py-20 px-6">
            <div className="w-16 h-16 bg-gray-50 dark:bg-gray-800 rounded-2xl flex items-center justify-center text-gray-300 dark:text-gray-600 mx-auto mb-4">
              <Search className="w-8 h-8" />
            </div>
            <p className="text-[11px] text-gray-400 font-bold uppercase tracking-widest leading-relaxed">
              {t('workflow.palette.noMatch', '未找到匹配组件')}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default NodePalette;
