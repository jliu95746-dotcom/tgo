import React from 'react';
import { XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getIconComponent, getIconColor } from '@/components/knowledge/IconPicker';
import type { KnowledgeBaseItem } from '@/types';
import { knowledgeBasePreview } from '@/utils/knowledgePreview';

interface AgentKnowledgeBaseRowProps {
  kb: KnowledgeBaseItem;
  onRemove: () => void;
  enabled?: boolean;
  disabled?: boolean;
}

const AgentKnowledgeBaseRow: React.FC<AgentKnowledgeBaseRowProps> = ({ kb, onRemove, enabled = true, disabled = false }) => {
  const { t } = useTranslation();
  const IconComponent = getIconComponent(kb.icon);
  const iconColor = enabled ? getIconColor(kb.icon) : 'text-gray-400';

  return (
    <div className={`flex items-center justify-between p-3 rounded-md border transition-colors ${
      enabled ? 'bg-white border-gray-200 dark:bg-gray-800 dark:border-gray-700' : 'bg-gray-50 border-gray-200 dark:bg-gray-900/50 dark:border-gray-800'
    }`}>
      <div className="flex items-start space-x-3 min-w-0 flex-1">
        <IconComponent className={`w-5 h-5 flex-shrink-0 mt-0.5 transition-colors duration-200 ${iconColor} hover:opacity-80`} />
        <div className="min-w-0">
          <div className={`text-sm font-medium ${enabled ? 'text-gray-800 dark:text-gray-100' : 'text-gray-500'} truncate`}>{kb.title}</div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-snug line-clamp-2 break-words">{knowledgeBasePreview(kb.content)}</div>
          <div className="text-xs text-gray-400 dark:text-gray-500 mt-1 truncate">
            {t('knowledge.filesCount', { count: kb.fileCount ?? 0, defaultValue: `${kb.fileCount ?? 0} 文件` })} • {kb.author}
          </div>
        </div>
      </div>
      <div className="flex items-center space-x-2 shrink-0">
        <button
          type="button"
          onClick={onRemove}
          className="text-gray-400 hover:text-red-500 transition-colors"
          disabled={disabled}
          title={t('common.remove', '移除')}
        >
          <XCircle className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

export default AgentKnowledgeBaseRow;
