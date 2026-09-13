import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AiToolResponse } from '@/types';
import { toolProbeApi, type ToolJson, type ToolTestResult } from '@/services/toolProbeApi';

export default function SavedToolTestModal({ tool, onClose }: { tool: AiToolResponse; onClose: () => void }) {
  const { t } = useTranslation();
  const [input, setInput] = useState('{}');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ToolTestResult | null>(null);
  const pending = useRef(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const run = async () => {
    if (!confirmed || pending.current) return;
    let data: Record<string, ToolJson>;
    try {
      const parsed: ToolJson = JSON.parse(input);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
      data = parsed;
    } catch { setResult({ success: false, error: t('tools.probe.invalidJson') }); return; }
    pending.current = true; setBusy(true); setResult(null);
    try {
      const response = await toolProbeApi.execute(tool.id, data);
      if (active.current) setResult(response);
    } catch { if (active.current) setResult({ success: false, error: t('tools.probe.failed') }); }
    finally { pending.current = false; if (active.current) setBusy(false); }
  };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <section role="dialog" aria-modal="true" aria-labelledby="saved-tool-test-title" className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white p-6 dark:bg-gray-900">
      <h2 id="saved-tool-test-title" className="text-lg font-bold text-gray-900 dark:text-white">{t('tools.probe.test')} · {tool.name}</h2>
      <div className="mt-4 space-y-4 overflow-y-auto">
        <p className="text-sm text-amber-700 dark:text-amber-300">{t('tools.probe.warning')}</p>
        <details className="text-sm text-gray-600 dark:text-gray-300"><summary>{t('tools.probe.parameters')}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(tool.config?.input_schema ?? tool.config?.inputSchema ?? tool.config?.parameters ?? {}, null, 2)}</pre></details>
        <label className="block text-sm text-gray-700 dark:text-gray-200">{t('tools.probe.input')}
          <textarea rows={6} value={input} disabled={busy} onChange={e => { setInput(e.target.value); setResult(null); }} className="mt-2 w-full rounded-lg border border-gray-300 bg-transparent p-3 font-mono dark:border-gray-700" />
        </label>
        <label className="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-300"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />{t('tools.probe.confirm')}</label>
        {result && <div role="status" className="rounded-lg bg-gray-100 p-3 dark:bg-gray-800">
          <p className={result.success ? 'text-green-600' : 'text-red-500'}>{t(result.success ? 'tools.probe.success' : 'tools.probe.failure')}</p>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs text-gray-700 dark:text-gray-200">{result.success ? JSON.stringify(result.output_data, null, 2) : result.error}</pre>
        </div>}
      </div>
      <div className="mt-5 flex justify-end gap-3">
        <button type="button" onClick={onClose} disabled={busy} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 dark:text-gray-300">{t('common.close', '关闭')}</button>
        <button type="button" onClick={run} disabled={busy || !confirmed} className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t(busy ? 'tools.probe.running' : 'tools.probe.run')}</button>
      </div>
    </section>
  </div>;
}
