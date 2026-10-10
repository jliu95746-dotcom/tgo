import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KnowledgeGovernanceApiService } from '@/services/knowledgeGovernanceApi';
import type { KnowledgeChannel, KnowledgeGovernanceRecord } from '@/types';

const channels: KnowledgeChannel[] = ['wecom_kf', 'web', 'app', 'phone', 'internal'];

export default function KnowledgeChannelEditor({ record, onSaved }: { record: KnowledgeGovernanceRecord; onSaved: () => Promise<void> }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<KnowledgeChannel[]>(record.channels);
  const [revision, setRevision] = useState(record.updated_at);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setSaving(true);
    setError('');
    try {
      await KnowledgeGovernanceApiService.updateChannels(record.id, {
        channels: selected, expected_updated_at: revision,
      });
      await onSaved();
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('knowledge.availability.saveFailed'));
    } finally { setSaving(false); }
  };
  return (
    <div className="text-xs">
      <button disabled={saving} className="rounded px-2 py-1 text-blue-600 hover:bg-blue-50" onClick={() => { setSelected(record.channels); setRevision(record.updated_at); setError(''); setOpen(!open); }}>{t('knowledge.availability.editChannels')}</button>
      {open && <div className="mt-2 rounded-lg border border-blue-200 bg-white p-3 dark:bg-gray-800">
        <p className="mb-2">{t('knowledge.availability.channelsHint')}</p>
        <div className="flex flex-wrap gap-3">{channels.map(channel => <label key={channel} className="inline-flex items-center gap-1">
          <input type="checkbox" disabled={saving} checked={selected.includes(channel)} onChange={event => setSelected(event.target.checked ? [...selected, channel] : selected.filter(c => c !== channel))} />
          {t(`knowledge.governance.channelNames.${channel}`)}
        </label>)}</div>
        {error && <p role="alert" className="mt-2 text-red-600">{error}</p>}
        <div className="mt-3 flex gap-3"><button disabled={saving || !selected.length} onClick={() => void save()} className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-50">{t('knowledge.availability.saveChannels')}</button>
          <button disabled={saving} onClick={() => setOpen(false)}>{t('common.cancel')}</button></div>
      </div>}
    </div>
  );
}
