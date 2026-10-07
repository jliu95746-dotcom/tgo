import React from 'react';
import { useTranslation } from 'react-i18next';
import { MessagePayloadType, type Message, type PayloadVoice } from '@/types';
import { useChatFileUrls } from '@/hooks/useChatFileUrls';

const VoiceMessage: React.FC<{ message: Message }> = ({ message }) => {
  const { t } = useTranslation();
  const payload = message.payload?.type === MessagePayloadType.VOICE
    ? message.payload as PayloadVoice
    : undefined;
  const [url] = useChatFileUrls([payload?.url || message.content]);

  return (
    <div className="max-w-xs rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
      <p className="mb-2 text-xs text-gray-600 dark:text-gray-300">
        {t('chat.messages.voice.label')}
      </p>
      {url ? (
        <audio controls preload="none" src={url} aria-label={t('chat.messages.voice.play')} className="w-60 max-w-full" />
      ) : (
        <p className="text-xs text-gray-500">{t('chat.messages.voice.unavailable')}</p>
      )}
    </div>
  );
};

export default VoiceMessage;
