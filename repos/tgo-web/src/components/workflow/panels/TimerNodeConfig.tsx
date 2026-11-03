import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { workflowEventOwner } from '@/stores/workflowEventTestStore';
import { useWorkflowScheduleStore } from '@/stores/workflowScheduleStore';
import { parseTimerForm, timerExpression, type TimerPreset } from '@/utils/workflowScheduleForm';
import type { TimerNodeData } from '@/types/workflow';
import WorkflowScheduleConfiguration, { scheduleTime } from './WorkflowScheduleConfiguration';

const zones = ['UTC', 'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Tokyo', 'America/New_York', 'Europe/London'];
export default function TimerNodeConfig({ data, onUpdate }: {
  data: TimerNodeData; onUpdate: (updates: Partial<TimerNodeData>) => void;
}) {
  const { t, i18n } = useTranslation();
  const workflow = useWorkflowStore(state => state.currentWorkflow);
  const owner = useAuthStore(state => workflowEventOwner(state.user));
  const store = useWorkflowScheduleStore();
  const { activate, clearPreview } = store;
  const form = parseTimerForm(data.cron_expression || '0 9 * * *');
  const [mode, setMode] = useState<TimerPreset>(form.mode);
  const lastWritten = useRef(data.cron_expression);
  const [customZone, setCustomZone] = useState(!zones.includes(data.timezone || 'UTC'));
  const workflowId = workflow?.id, publication = workflow?.publication_revision;
  useEffect(() => {
    if (owner && workflowId) return activate(owner, workflowId);
    return undefined;
  }, [activate, owner, workflowId, publication]);
  useEffect(() => {
    clearPreview();
    if (lastWritten.current !== data.cron_expression) setMode(parseTimerForm(data.cron_expression).mode);
  }, [data.cron_expression, data.timezone, clearPreview]);
  function update(expression: string | null) {
    if (expression !== null) { lastWritten.current = expression; onUpdate({ cron_expression: expression }); }
  }
  const field = 'w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-100';
  const label = 'block space-y-2 text-xs font-semibold text-gray-600 dark:text-gray-300';
  const expression = data.cron_expression || '', zone = data.timezone ?? 'UTC';
  const active = owner && store.ownerKey === owner && store.workflowId === workflowId;
  return <div className="space-y-5">
    <p className="text-xs leading-5 text-gray-500">{t('workflowSchedule.draftHint')}</p>
    <label className={label}><span>{t('workflowSchedule.frequency')}</span><select className={field} value={mode} onChange={event => {
      const value = event.target.value as TimerPreset; setMode(value);
      if (value !== 'custom') update(timerExpression(value, form.time, form.weekday));
    }}>{(['hourly', 'daily', 'weekdays', 'weekly', 'custom'] as const).map(value => <option value={value} key={value}>{t(`workflowSchedule.modes.${value}`)}</option>)}</select></label>
    {mode === 'hourly' && <label className={label}><span>{t('workflowSchedule.minute')}</span><select className={field} value={Number(form.time.split(':')[1])} onChange={event => update(timerExpression(mode, `09:${event.target.value.padStart(2, '0')}`, form.weekday))}>{Array.from({ length: 60 }, (_, minute) => <option key={minute} value={minute}>{minute}</option>)}</select></label>}
    {mode !== 'custom' && mode !== 'hourly' && <label className={label}><span>{t('workflowSchedule.time')}</span><input type="time" className={field} value={form.time} onChange={event => update(timerExpression(mode, event.target.value, form.weekday))} /></label>}
    {mode === 'weekly' && <label className={label}><span>{t('workflowSchedule.weekday')}</span><select className={field} value={form.weekday} onChange={event => update(timerExpression(mode, form.time, event.target.value))}>{Array.from({ length: 7 }, (_, day) => <option key={day} value={day}>{t(`workflowSchedule.days.${day}`)}</option>)}</select></label>}
    {mode === 'custom' && <label className={label}><span>{t('workflowSchedule.expression')}</span><input className={`${field} font-mono`} maxLength={512} value={expression} onChange={event => update(event.target.value)} /><span className="block text-[11px] font-normal leading-5 text-gray-500">{t('workflowSchedule.ruleHint')}</span></label>}
    <label className={label}><span>{t('workflowSchedule.timezone')}</span><select className={field} value={customZone ? 'custom' : zone} onChange={event => {
      setCustomZone(event.target.value === 'custom');
      if (event.target.value !== 'custom') onUpdate({ timezone: event.target.value });
    }}>{zones.map(value => <option key={value} value={value}>{t(`workflowSchedule.zones.${value}`)}</option>)}<option value="custom">{t('workflowSchedule.customZone')}</option></select></label>
    {customZone && <input aria-label={t('workflowSchedule.timezone')} className={field} maxLength={100} value={zone} onChange={event => onUpdate({ timezone: event.target.value })} />}
    <p className="break-all text-xs text-gray-500"><code>{expression}</code> · {zone}</p>
    {!owner && <p className="text-xs text-gray-500">{t('workflowSchedule.adminOnly')}</p>}
    {active && <>
      <button type="button" disabled={store.previewing || !expression || !zone} onClick={() => void store.requestPreview({ cron_expression: expression, timezone: zone })} className="rounded-xl border border-blue-200 px-3 py-2 text-xs font-semibold text-blue-600 disabled:opacity-40 dark:border-blue-800 dark:text-blue-300">{t('workflowSchedule.previewAction')}</button>
      <p className="text-[11px] leading-5 text-gray-500">{t('workflowSchedule.previewHint')}</p>
      {store.previewError && <p role="alert" className="break-words text-xs text-red-600 dark:text-red-400">{store.previewError}</p>}
      {store.preview && <ol className="space-y-1 rounded-xl bg-gray-50 p-3 text-xs text-gray-700 dark:bg-gray-800/50 dark:text-gray-200">{store.preview.preview_at.map(value => <li key={value}>{scheduleTime(value, store.preview!.timezone, i18n.language)}</li>)}</ol>}
      <WorkflowScheduleConfiguration />
    </>}
  </div>;
}
