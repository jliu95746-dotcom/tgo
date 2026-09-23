import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download } from 'lucide-react';
import { WuKongIMApiService } from '@/services/wukongimApi';
import { collectConversationExport } from '@/services/conversationExport';

export default function ConversationExport({ channelId, channelType }: { channelId: string; channelType: number }) {
  const { t } = useTranslation();
  const generation = useRef(0);
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => () => { generation.current++; }, [channelId, channelType]);
  const download = async () => {
    if (running.current) return;
    const current = generation.current;
    running.current = true;
    setBusy(true); setError('');
    try {
      const messages = await collectConversationExport(channelId, channelType,
        cursor => cursor === 0
          ? WuKongIMApiService.getChannelHistory(channelId, channelType, 100)
          : WuKongIMApiService.loadMoreMessages(channelId, channelType, cursor, 100),
        { cancelled: () => generation.current !== current });
      const artifact = { format: 'yujian-conversation-v1', exported_at: new Date().toISOString(),
        scope: t('billingSupport.exportScope'), channel_id: channelId, channel_type: channelType, messages };
      const url = URL.createObjectURL(new Blob([JSON.stringify(artifact, null, 2)], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `yujian-conversation-${new Date().toISOString().replace(/:/g, '-')}.json`;
      document.body.appendChild(link);
      link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (caught) {
      if (generation.current === current) setError(caught instanceof Error && caught.message === 'EXPORT_LIMIT'
        ? t('billingSupport.exportLimit') : t('billingSupport.exportFailed'));
    } finally {
      running.current = false;
      if (generation.current === current) setBusy(false);
    }
  };
  return <div className="relative">
    <button type="button" disabled={busy} title={t('billingSupport.exportScope')} onClick={() => void download()} className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-xs hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-700">
      <Download className="h-4 w-4" aria-hidden="true" />{t(busy ? 'billingSupport.exportBusy' : 'billingSupport.exportConversation')}
    </button>
    {error && <p role="alert" className="absolute right-0 top-full z-20 w-64 rounded-lg border bg-white p-3 text-sm text-red-700 shadow-lg">{error}</p>}
  </div>;
}
