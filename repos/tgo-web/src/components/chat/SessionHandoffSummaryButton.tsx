import { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, Loader2, RefreshCw, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { sessionSummaryApi } from '@/services/sessionSummaryApi';
import type { SessionHandoffExcerpt, SessionHandoffSummary } from '@/types/sessionHandoff';

interface Props {
  visitorId: string;
}

export default function SessionHandoffSummaryButton({ visitorId }: Props) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [summary, setSummary] = useState<SessionHandoffSummary | null>(null);
  const [error, setError] = useState('');
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  const load = useCallback(async () => {
    requestRef.current?.abort();
    const request = new AbortController();
    requestRef.current = request;
    setIsLoading(true);
    setError('');
    try {
      const result = await sessionSummaryApi.current(visitorId, request.signal);
      if (!request.signal.aborted) setSummary(result);
    } catch (cause: unknown) {
      if (!request.signal.aborted) {
        setError(cause instanceof Error ? cause.message : t('chat.header.summaryLoadError'));
      }
    } finally {
      if (!request.signal.aborted) setIsLoading(false);
    }
  }, [visitorId, t]);

  const toggle = () => {
    if (isOpen) {
      setIsOpen(false);
      requestRef.current?.abort();
      return;
    }
    setIsOpen(true);
    void load();
  };

  const renderExcerpts = (items: SessionHandoffExcerpt[]) => items.length ? (
    <ul className="space-y-2">
      {items.map(item => (
        <li key={item.message_seq} className="rounded-md bg-gray-50 px-2.5 py-2 text-sm leading-5 text-gray-700 dark:bg-gray-900/50 dark:text-gray-200">
          <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">
            {item.role === 'customer'
              ? t('chat.header.summaryCustomer')
              : item.role === 'assistant'
                ? t('chat.header.summaryAssistant')
                : t('chat.header.summaryStaff')}
          </span>
          <span className="break-words">{item.content}</span>
        </li>
      ))}
    </ul>
  ) : <p className="text-sm text-gray-500 dark:text-gray-400">{t('chat.header.summaryEmpty')}</p>;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={isOpen}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/20"
      >
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        {t('chat.header.summaryButton')}
      </button>
      {isOpen && (
        <section className="absolute right-0 top-full z-30 mt-2 max-h-[70vh] w-[min(22rem,calc(100vw-1rem))] overflow-y-auto rounded-xl border border-gray-200 bg-white p-4 shadow-xl dark:border-gray-700 dark:bg-gray-800" aria-label={t('chat.header.summaryTitle')}>
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-semibold text-gray-900 dark:text-white">{t('chat.header.summaryTitle')}</h3>
            <div className="flex gap-1">
              <button type="button" onClick={() => void load()} disabled={isLoading} aria-label={t('chat.header.summaryRefresh')} className="rounded p-1 text-gray-500 hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-700"><RefreshCw className="h-4 w-4" /></button>
              <button type="button" onClick={toggle} aria-label={t('chat.header.summaryClose')} className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"><X className="h-4 w-4" /></button>
            </div>
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">{t('chat.header.summaryScope')}</p>
          {isLoading && <p className="mt-4 flex items-center gap-2 text-sm text-gray-500"><Loader2 className="h-4 w-4 animate-spin" />{t('chat.header.summaryLoading')}</p>}
          {error && <p role="alert" className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
          {!isLoading && !error && summary && (
            <div className="mt-4 space-y-4">
              <div><h4 className="mb-2 text-xs font-medium text-gray-500 dark:text-gray-400">{t('chat.header.summaryQuestions')}</h4>{renderExcerpts(summary.latest_customer_messages)}</div>
              <div><h4 className="mb-2 text-xs font-medium text-gray-500 dark:text-gray-400">{t('chat.header.summaryReplies')}</h4>{renderExcerpts(summary.latest_replies)}</div>
              {summary.has_earlier_messages && <p className="text-xs text-amber-700 dark:text-amber-300">{t('chat.header.summaryMore')}</p>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
