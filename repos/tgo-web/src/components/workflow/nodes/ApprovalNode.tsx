import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { ApprovalNodeData } from '@/types/workflow';
import NodeExecutionOverlay from '../overlays/NodeExecutionOverlay';

function ApprovalNode({ id, data, selected }: NodeProps<ApprovalNodeData>) {
  const { t } = useTranslation();
  const label = data.label || t('workflow.node_types.approval.label');
  return (
    <div className={`relative min-w-[240px] rounded-2xl border bg-white px-5 py-4 shadow-sm transition-all dark:bg-gray-800 ${
      selected ? 'border-purple-500 ring-4 ring-purple-500/10' : 'border-gray-100 hover:shadow-md dark:border-gray-700'
    }`}>
      <NodeExecutionOverlay nodeId={id} label={label} />
      <div className="absolute bottom-4 left-0 top-4 w-1 rounded-r-full bg-purple-500" />
      <Handle type="target" position={Position.Top} className="!h-3 !w-3 !border-2 !border-white !bg-purple-500" />
      <div className="flex items-center gap-3">
        <div className="rounded-xl bg-purple-50 p-2.5 text-purple-600 dark:bg-purple-900/20 dark:text-purple-400">
          <ShieldCheck className="h-6 w-6" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-bold text-gray-900 dark:text-gray-100">{label}</p>
          <p className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
            {data.summary || t('businessScenarios.workflow.approvalConfig.emptySummary')}
          </p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-3 !w-3 !border-2 !border-white !bg-purple-500" />
    </div>
  );
}

export default memo(ApprovalNode);
