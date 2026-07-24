import React from 'react';
import { useTranslation } from 'react-i18next';
import Icon from '@/components/ui/Icon';

export interface ChatListEmptyProps { isSyncing: boolean; }

/**
 * Empty state when there are no conversations to show.
 */
export const ChatListEmpty: React.FC<ChatListEmptyProps> = React.memo(({ isSyncing }) => {
  const { t } = useTranslation();
  if (isSyncing) return null;
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 text-gray-600 dark:text-gray-300">
      <Icon name="MessageCircle" size={40} className="w-10 h-10 mb-3 text-gray-400 dark:text-gray-500" />
      <p className="text-sm">{t('chat.list.emptyCurrentView', '当前列表为空')}</p>
    </div>
  );
});

ChatListEmpty.displayName = 'ChatListEmpty';

