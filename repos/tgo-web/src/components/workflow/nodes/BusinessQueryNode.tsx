import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { BusinessQueryNodeData } from '@/types/workflow';
import NodeExecutionOverlay from '../overlays/NodeExecutionOverlay';

function BusinessQueryNode({ id, data, selected }: NodeProps<BusinessQueryNodeData>) {
  const { t } = useTranslation();
  return <div className={`relative flex min-w-[220px] items-center gap-4 rounded-2xl border bg-white px-5 py-4 shadow-sm dark:bg-gray-800 ${selected ? 'border-blue-500 ring-4 ring-blue-500/10' : 'border-gray-100 dark:border-gray-700'}`}>
    <NodeExecutionOverlay nodeId={id} label={data.label || t('workflow.node_types.business_query.label')} />
    <div className="absolute bottom-4 left-0 top-4 w-1 rounded-r-full bg-blue-500" />
    <div className="rounded-xl bg-blue-50 p-2.5 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400"><ShieldCheck className="h-6 w-6" /></div>
    <div className="min-w-0">
      <div className="truncate text-sm font-bold text-gray-900 dark:text-gray-100">{data.label || t('workflow.node_types.business_query.label')}</div>
      <div className="mt-1 text-xs text-blue-600 dark:text-blue-400">{t(`businessScenarios.workflow.queryOperations.${data.operation}`)}</div>
    </div>
    <Handle type="target" position={Position.Top} className="!h-3 !w-3 !border-2 !border-white !bg-blue-500" />
    <Handle type="source" position={Position.Bottom} className="!h-3 !w-3 !border-2 !border-white !bg-blue-500" />
  </div>;
}

export default memo(BusinessQueryNode);
