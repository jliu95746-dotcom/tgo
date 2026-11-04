import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { EventNodeData } from '@/types/workflow';
import SessionClosedTemplateAction from './SessionClosedTemplateAction';

export default function EventNodeConfig({ data, onUpdate }: {
  data: EventNodeData; onUpdate: (updates: Partial<EventNodeData>) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const automatic = data.event_type === 'staff.session.closed';
  const field = 'w-full rounded-xl border border-gray-100 bg-gray-50 px-4 py-2 text-sm text-gray-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-100';
  return <div className="space-y-4">
    <div className="space-y-2">
      <label htmlFor={id} className="text-[11px] font-bold text-gray-400">{t('workflowEvents.sourceConfig.label')}</label>
      <select id={id} value={automatic ? 'staff.session.closed' : 'manual'} onChange={event => onUpdate({ event_type: event.target.value === 'manual' ? '' : 'staff.session.closed' })} className={field}>
        <option value="manual">{t('workflowEvents.sourceConfig.manual')}</option>
        <option value="staff.session.closed">{t('workflowEvents.subscription.source')}</option>
      </select>
    </div>
    {automatic ? <div className="space-y-2 rounded-xl bg-blue-50 p-3 text-xs leading-5 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">
      <p>{t('workflowEvents.sourceConfig.autoHint')}</p>
      <p className="break-all font-mono">staff.session.closed</p>
    </div> : <div className="space-y-2">
      <label htmlFor={`${id}-type`} className="text-[11px] font-bold text-gray-400">{t('workflowEvents.eventType')}</label>
      <input id={`${id}-type`} value={data.event_type} onChange={event => onUpdate({ event_type: event.target.value })} placeholder={t('workflowEvents.sourceConfig.placeholder')} className={`${field} font-mono`} />
      <p className="text-xs leading-5 text-gray-500">{t('workflowEvents.sourceConfig.manualHint')}</p>
    </div>}
    <SessionClosedTemplateAction />
  </div>;
}
