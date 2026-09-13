import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { FiChevronDown, FiEdit2, FiTrash2 } from 'react-icons/fi';

interface Props { modelId: string; onEdit: () => void; onDelete: () => void; disabled?: boolean; }
export default function ModelRowActions({ modelId, onEdit, onDelete, disabled }: Props) {
  const { t } = useTranslation();
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const close = () => { setPosition(null); trigger.current?.focus(); };
  useEffect(() => {
    if (!position) return;
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const dismiss = () => setPosition(null);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', dismiss, true);
    return () => { window.removeEventListener('resize', dismiss); window.removeEventListener('scroll', dismiss, true); };
  }, [position]);
  return <>
    <button ref={trigger} type="button" disabled={disabled} aria-label={t('modelSetup.moreFor', { model: modelId })} aria-haspopup="menu" aria-expanded={!!position}
      onClick={() => {
        if (position) { close(); return; }
        const rect = trigger.current?.getBoundingClientRect();
        if (rect) setPosition({ left: Math.max(8, Math.min(rect.right - 144, window.innerWidth - 152)), top: rect.bottom + 106 > window.innerHeight ? Math.max(8, rect.top - 100) : rect.bottom + 6 });
      }} className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap disabled:opacity-40">
      {t('modelSetup.more')}<FiChevronDown />
    </button>
    {position && createPortal(<div className="fixed inset-0 z-[70]" onClick={close}>
      <div ref={menu} role="menu" aria-label={t('modelSetup.moreFor', { model: modelId })} style={position}
        className="fixed w-36 p-1.5 rounded-lg bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-600 shadow-xl"
        onClick={event => event.stopPropagation()} onKeyDown={event => {
          if (event.key === 'Escape' || event.key === 'Tab') { close(); return; }
          if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
          event.preventDefault();
          const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button') || []);
          const index = items.findIndex(item => item === document.activeElement);
          items[(index + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
        }}>
        <button role="menuitem" onClick={() => { close(); onEdit(); }} className="w-full flex items-center gap-2 rounded px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 focus:bg-gray-100 dark:focus:bg-gray-800"><FiEdit2 />{t('common.edit')}</button>
        <button role="menuitem" onClick={() => { close(); onDelete(); }} className="w-full flex items-center gap-2 rounded px-3 py-2 text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 focus:bg-red-50 dark:focus:bg-red-900/20"><FiTrash2 />{t('common.delete')}</button>
      </div>
    </div>, document.body)}
  </>;
}
