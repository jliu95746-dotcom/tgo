import { useState } from 'react';
import { Menu, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { useCompactChatLayout } from '@/hooks/useCompactChatLayout';
import Sidebar from './Sidebar';

export default function MobileChatNavigation() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const module = pathname.split('/')[1];
  const title = module === 'chat' ? t('mobileChat.workspace') : t(`navigation.${module === 'platforms' ? 'channels' : module}`);
  const [open, setOpen] = useState(false);
  const compact = useCompactChatLayout();
  const dialog = useDialogFocus<HTMLDivElement>(() => setOpen(false), open && compact);
  return <>
    <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 bg-white px-2 py-1 dark:border-gray-700 dark:bg-gray-900 min-[900px]:hidden">
      <button type="button" onClick={() => setOpen(true)} aria-label={t('mobileChat.navigation')} aria-expanded={open && compact} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-gray-700 hover:bg-gray-100 dark:text-gray-200">
        <Menu className="h-5 w-5" />
      </button>
      <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{title}</span>
    </div>
    {open && compact && <div className="fixed inset-0 z-[120]">
      <button type="button" aria-label={t('mobileChat.closeNavigation')} onClick={() => setOpen(false)} className="absolute inset-0 bg-gray-950/40" />
      <div ref={dialog} role="dialog" aria-modal="true" aria-label={t('mobileChat.navigation')} className="absolute inset-y-0 left-0 flex w-[min(18rem,calc(100vw-2rem))] flex-col overflow-y-auto bg-white shadow-xl dark:bg-gray-900">
        <div className="flex justify-end p-2"><button type="button" aria-label={t('mobileChat.closeNavigation')} onClick={() => setOpen(false)} className="flex h-10 w-10 items-center justify-center rounded-lg hover:bg-gray-100 dark:text-gray-200"><X className="h-5 w-5" /></button></div>
        <Sidebar expanded onNavigate={() => setOpen(false)} />
      </div>
    </div>}
  </>;
}
