import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toolProbeApi, type MCPConnection, type DiscoveredTool } from '@/services/toolProbeApi';

interface Props { connection: MCPConnection; disabled: boolean; onSelect: (tool: DiscoveredTool) => void }

export default function MCPDiscoveryField({ connection, disabled, onSelect }: Props) {
  const { t } = useTranslation();
  const [tools, setTools] = useState<DiscoveredTool[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  const pending = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const discover = async () => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setMessage(''); setTools([]);
    try {
      const result = await toolProbeApi.discover(connection);
      if (!active.current) return;
      setTools(result.success ? result.tools : []);
      setMessage(result.success ? t(result.tools.length ? 'tools.probe.connected' : 'tools.probe.empty') : result.error || t('tools.probe.failed'));
    } catch {
      if (active.current) setMessage(t('tools.probe.failed'));
    } finally {
      pending.current = false;
      if (active.current) setBusy(false);
    }
  };
  return <div className="space-y-3 rounded-xl border border-blue-200 p-4 dark:border-blue-900">
    <p className="text-sm text-gray-500">{t('tools.probe.discoveryHint')}</p>
    <button type="button" disabled={disabled || busy || !connection.endpoint.trim()} onClick={discover} className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t(busy ? 'tools.probe.connecting' : 'tools.probe.connect')}</button>
    {message && <p role="status" className="text-sm text-gray-600 dark:text-gray-300">{message}</p>}
    {tools.length > 0 && <select aria-label={t('tools.probe.choose')} defaultValue="" disabled={disabled || busy} onChange={e => { const tool = tools.find(item => item.name === e.target.value); if (tool) onSelect(tool); }} className="w-full rounded-lg border border-gray-300 bg-white p-2 text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-white">
      <option value="" disabled>{t('tools.probe.choose')}</option>
      {tools.map(tool => <option key={tool.name} value={tool.name}>{tool.name}{tool.description ? ` — ${tool.description.slice(0, 80)}` : ''}</option>)}
    </select>}
  </div>;
}
