import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  typeName: string;
  existingNames: string[];
  onClose: () => void;
  onCreate: (name: string) => Promise<void>;
}

export default function PlatformNameModal({ typeName, existingNames, onClose, onCreate }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const cleanName = name.trim();
  const duplicate = existingNames.some(value => value.trim().toLocaleLowerCase() === cleanName.toLocaleLowerCase());
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <form role="dialog" aria-modal="true" aria-labelledby="channel-name-title"
        className="w-full max-w-md rounded-xl bg-white dark:bg-gray-800 p-6 shadow-xl"
        onSubmit={async event => {
          event.preventDefault();
          if (!cleanName || duplicate || busy) return;
          setBusy(true);
          try { await onCreate(cleanName); } finally { setBusy(false); }
        }}>
        <h3 id="channel-name-title" className="text-lg font-semibold text-gray-900 dark:text-white">{t('channelManagement.nameTitle')}</h3>
        <p className="mt-2 text-sm text-gray-500">{typeName} · {t('channelManagement.nameHint')}</p>
        <label htmlFor="channel-name" className="block mt-5 mb-2 text-sm dark:text-gray-200">{t('channelManagement.nameLabel')}</label>
        <input id="channel-name" autoFocus required maxLength={100} value={name} disabled={busy}
          onChange={event => setName(event.target.value)} placeholder={t('channelManagement.namePlaceholder')}
          className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 dark:text-white px-3 py-2" />
        {cleanName && duplicate && <p role="alert" className="mt-2 text-sm text-red-500">{t('channelManagement.duplicateName')}</p>}
        <div className="flex justify-end gap-3 mt-6">
          <button type="button" disabled={busy} onClick={onClose} className="px-4 py-2 rounded-lg border dark:text-gray-200">{t('common.cancel')}</button>
          <button type="submit" disabled={!cleanName || duplicate || busy} className="px-4 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-50">{t(busy ? 'channelManagement.creating' : 'channelManagement.create')}</button>
        </div>
      </form>
    </div>
  );
}
