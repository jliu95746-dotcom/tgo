/**
 * Timer Node Component
 * Scheduled trigger for workflow
 */

import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { TimerNodeData } from '@/types/workflow';
import NodeExecutionOverlay from '../overlays/NodeExecutionOverlay';

const TimerNode: React.FC<NodeProps<TimerNodeData>> = ({ id, data, selected }) => {
  const { t } = useTranslation();
  const defaultLabel = t('workflow.node_types.timer.label', '定时触发');

  return (
    <div
      className={`
        px-5 py-4 rounded-2xl bg-white dark:bg-gray-800 shadow-sm border
        flex items-center gap-4 min-w-[200px] transition-all duration-200 relative
        ${selected 
          ? 'border-green-500 ring-4 ring-green-500/10' 
          : 'border-gray-100 dark:border-gray-700 hover:shadow-md'
        }
      `}
    >
      <NodeExecutionOverlay nodeId={id} label={data.label || defaultLabel} />
      <div className="absolute left-0 top-4 bottom-4 w-1 bg-green-500 rounded-r-full" />

      <div className="p-2.5 rounded-xl bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400">
        <Clock className="w-6 h-6" />
      </div>
      
      <div className="min-w-0">
        <div className="text-sm font-bold text-gray-900 dark:text-gray-100">
          {data.label || defaultLabel}
        </div>
        <div className="text-[10px] text-amber-600 dark:text-amber-400 font-bold uppercase tracking-wider flex items-center gap-1 mt-0.5">
          <Clock className="w-2.5 h-2.5" />
          {data.cron_expression || t('workflow.node_display.scheduled', 'Scheduled')}
        </div>
        <p className="mt-1 text-[10px] text-gray-500 dark:text-gray-400">{data.timezone || 'UTC'}</p>
      </div>
      
      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-3 !h-3 !bg-green-500 !border-2 !border-white dark:!border-gray-800 !shadow-sm"
      />
    </div>
  );
};

export default memo(TimerNode);

