import React from 'react';
import { useTranslation } from 'react-i18next';
import Icon from '../ui/Icon';

/**
 * Props for the EmptyState component
 */
export interface EmptyStateProps {
  /** Type of empty state to display */
  type: 'no-chat' | 'no-messages';
  /** Whether the current list contains conversations waiting to be selected */
  hasConversations?: boolean;
}

/**
 * Component for displaying empty states in the chat
 * Memoized to prevent unnecessary re-renders
 */
const EmptyState: React.FC<EmptyStateProps> = React.memo(({ type, hasConversations = false }) => {
  const { t } = useTranslation();

  if (type === 'no-chat') {
    return (
      <main className="flex-grow min-w-0 flex items-center justify-center bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-800">
        <div className="max-w-sm px-8 text-center text-gray-600 dark:text-gray-300">
          <Icon name="MessageCircle" size={48} className="mx-auto mb-4 text-gray-400 dark:text-gray-500" />
          <h1 className="text-base font-medium text-gray-700 dark:text-gray-200">
            {hasConversations
              ? t('chat.empty.selectConversation', '选择一个聊天开始对话')
              : t('chat.sync.noConversations', '暂无对话')}
          </h1>
          <p className="mt-2 text-sm leading-6">
            {hasConversations
              ? t('chat.empty.selectConversationDescription', '从左侧列表选择需要处理的会话')
              : t('chat.empty.waitingForConversation', '新会话接入后，将显示在左侧列表中')}
          </p>
        </div>
      </main>
    );
  }

  if (type === 'no-messages') {
    return (
      <div className="flex items-center justify-center h-32">
        <p className="text-gray-500 dark:text-gray-400">
          {t('chat.history.noMessages', '暂无历史消息')}
        </p>
      </div>
    );
  }

  return null;
});

EmptyState.displayName = 'EmptyState';

export default EmptyState;
