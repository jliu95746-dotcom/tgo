import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { OperationsAudit, OperationsTask } from '../../types/operationsTasks';

const button = 'rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-40';

export default function OperationsTasks() {
  const { t, i18n } = useTranslation();
  const text = (key: string) => t(`billingSupport.${key}`);
  const [opened, setOpened] = useState(false);
  const [tab, setTab] = useState<'tasks' | 'audits'>('tasks');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [tasks, setTasks] = useState<OperationsTask[]>([]);
  const [audits, setAudits] = useState<OperationsAudit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<OperationsTask | null>(null);
  const [reason, setReason] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!opened) return;
    let active = true;
    setBusy(true); setError('');
    const load = async () => {
      try {
        if (tab === 'tasks') { const data = await operationsApi.tasks(offset); if (active) setTasks(data); }
        else { const data = await operationsApi.audits(offset); if (active) setAudits(data); }
      } catch (caught) { if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
      finally { if (active) setBusy(false); }
    };
    void load();
    return () => { active = false; };
  }, [opened, tab, offset, revision, t]);
  useEffect(() => { if (selected) dialog.current?.showModal(); else dialog.current?.close(); }, [selected]);
  const retry = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected || busy) return;
    setBusy(true); setError('');
    try { await operationsApi.retryTask(selected.id, reason.trim()); setSelected(null); setRevision(value => value + 1); }
    catch (caught) { setError(caught instanceof Error ? caught.message : text('error')); }
    finally { setBusy(false); }
  };
  const date = (value: string) => new Date(value).toLocaleString(i18n.language);
  const count = tab === 'tasks' ? tasks.length : audits.length;
  return <section className="mt-8 space-y-4 border-t pt-8">
    <h2 className="text-2xl font-semibold">{text('taskTitle')}</h2>
    {!opened ? <button className={button} onClick={() => setOpened(true)}>{text('open')}</button> : <>
      <div className="flex flex-wrap gap-2">{(['tasks', 'audits'] as const).map(item => <button key={item} className={button} disabled={busy} aria-pressed={item === tab} onClick={() => { setTab(item); setOffset(0); }}>{text(item)}</button>)}<button className={button} disabled={busy} onClick={() => setRevision(value => value + 1)}>{text('refresh')}</button></div>
      {error && !selected && <p role="alert" className="text-red-700">{error}</p>}
      {tab === 'tasks' ? tasks.map(task => <article key={task.id} className="space-y-2 rounded-xl border p-4 text-sm">
        <p>{t(`billingSupport.taskKind.${task.kind}`, { defaultValue: task.kind })} · {t(`billingSupport.status.${task.status}`, { defaultValue: task.status })}</p>
        <p className="break-all font-mono text-xs">{task.id}</p>
        <p className="break-all text-xs text-slate-500">{text('company')}: {task.project_id || '—'} · {text('order')}: {task.order_id || '—'}</p>
        <p>{text('attempts')}: {task.attempts} · {text('scheduled')}: {date(task.available_at)}</p>
        {task.last_error && <p className="text-amber-800">{task.last_error}</p>}
        {task.status === 'pending' && task.last_error && <button className={button} disabled={busy} onClick={() => { setReason(''); setSelected(task); }}>{text('retryTask')}</button>}
      </article>) : audits.map(audit => <article key={audit.id} className="space-y-2 rounded-xl border p-4 text-sm"><p>{audit.action} · {date(audit.created_at)}</p><p>{audit.reason}</p><p className="break-all text-xs text-slate-500">{text('operator')}: {audit.operator_id} · {text('company')}: {audit.project_id || '—'}</p></article>)}
      {!count && !busy && <p>{text('empty')}</p>}
      <div className="flex justify-end gap-2"><button className={button} disabled={busy || !offset} onClick={() => setOffset(Math.max(0, offset - 50))}>{text('previous')}</button><button className={button} disabled={busy || count < 50} onClick={() => setOffset(offset + 50)}>{text('next')}</button></div>
    </>}
    <dialog ref={dialog} className="w-[min(92vw,480px)] rounded-2xl p-6 backdrop:bg-black/40" onCancel={event => { if (busy) event.preventDefault(); else setSelected(null); }}>
      <form onSubmit={event => void retry(event)} className="space-y-4"><h3 className="text-xl font-semibold">{text('retryTask')}</h3><p className="text-sm">{text('retryHint')}</p><p className="break-all font-mono text-xs">{selected?.id}</p><label className="block text-sm">{text('reason')}<textarea required minLength={5} maxLength={500} value={reason} onChange={event => setReason(event.target.value)} className="mt-2 w-full rounded-lg border p-3" /></label>{error && <p role="alert" className="text-red-700">{error}</p>}<div className="flex justify-end gap-2"><button type="button" className={button} disabled={busy} onClick={() => setSelected(null)}>{text('cancel')}</button><button className={button} disabled={busy || reason.trim().length < 5}>{text('confirmChange')}</button></div></form>
    </dialog>
  </section>;
}
