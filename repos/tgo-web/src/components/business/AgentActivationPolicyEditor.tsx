import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAgentActivationStore } from '@/stores/agentActivationStore';
import type { AgentActivationSelection } from '@/types/agentActivationPolicies';
import type { KnowledgeChannel } from '@/types/scenarioResources';

const channels: KnowledgeChannel[] = ['web', 'wecom_kf', 'app', 'phone', 'internal'];
export default function AgentActivationPolicyEditor({ agentId, disabled = false }: { agentId: string; disabled?: boolean }) {
  const { t } = useTranslation(), id = useId();
  const store = useAgentActivationStore();
  const entry = store.entries[agentId];
  const [selected, setSelected] = useState<AgentActivationSelection['template_id'] | ''>('');
  const [channel, setChannel] = useState<KnowledgeChannel>('web');
  const [removing, setRemoving] = useState<KnowledgeChannel | null>(null);
  const loadTemplates = store.loadTemplates;
  useEffect(() => { void loadTemplates(); }, [loadTemplates]);
  const active = entry?.policies?.policies.filter(row => row.enabled) ?? [];
  const available = store.templates.filter(row => row.available);
  const template = available.find(row => row.id === selected);
  const locked = disabled || !entry?.policiesReady || !entry.readinessReady || entry.readiness?.enabled
    || entry.loading || entry.writing || entry.enabling || Boolean(entry.pending);
  const save = async () => {
    if (locked || !template || !selected || !store.templatesReady) return;
    if (await store.save(agentId, { template_id: selected, template_version: template.version, channel })) await store.load(agentId, true);
  };
  const remove = async () => {
    if (locked || !removing) return;
    const target = removing;
    setRemoving(null);
    if (await store.remove(agentId, target)) await store.load(agentId, true);
  };
  return <section className="space-y-3 border-t border-gray-100 pt-4 dark:border-gray-800">
    <h4 className="text-sm font-bold text-gray-800 dark:text-gray-100">{t('businessScenarios.activation.policyTitle')}</h4>
    <p className="text-xs leading-5 text-gray-500 dark:text-gray-400">{t('businessScenarios.activation.policyHint')}</p>
    {entry?.readiness?.enabled && <p className="text-xs text-amber-700 dark:text-amber-300">{t('businessScenarios.activation.pauseBeforeEdit')}</p>}
    <ul className="space-y-2">
      {active.map(row => <li key={row.channel} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-gray-50 px-3 py-2 text-xs dark:bg-gray-800">
        <span>{t(`businessScenarios.scenes.${row.template_id}.title`)} · {t(`businessScenarios.activation.channels.${row.channel}`)}</span>
        <div className="flex gap-3"><button type="button" disabled={locked} onClick={() => { setSelected(row.template_id); setChannel(row.channel); }} className="text-blue-600 disabled:opacity-50 dark:text-blue-300">{t('businessScenarios.activation.changePolicy')}</button>
          <button type="button" disabled={locked} onClick={() => setRemoving(row.channel)} className="text-red-600 disabled:opacity-50 dark:text-red-400">{t('businessScenarios.activation.removePolicy')}</button></div>
      </li>)}
    </ul>
    {removing && <div role="alert" className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-900/20 dark:text-amber-200">
      <p>{t('businessScenarios.activation.removeConfirm', { channel: t(`businessScenarios.activation.channels.${removing}`) })}</p>
      <div className="flex gap-4"><button type="button" disabled={locked} onClick={() => void remove()} className="font-medium text-red-600">{t('businessScenarios.activation.confirmRemove')}</button><button type="button" onClick={() => setRemoving(null)}>{t('common.cancel')}</button></div>
    </div>}
    {!store.templatesReady && <p role="status" className="text-xs text-gray-500">{t(`businessScenarios.activation.${store.templatesLoading ? 'templatesLoading' : 'templatesFailed'}`)} <button type="button" disabled={store.templatesLoading} onClick={() => void loadTemplates(true)} className="text-blue-600 disabled:opacity-50">{t('businessScenarios.activation.refresh')}</button></p>}
    <fieldset disabled={locked || !store.templatesReady || Boolean(removing)} className="grid gap-3 sm:grid-cols-2 disabled:opacity-60">
      <label htmlFor={`${id}-role`} className="space-y-1 text-xs text-gray-600 dark:text-gray-300"><span>{t('businessScenarios.activation.role')}</span><select id={`${id}-role`} value={selected} onChange={event => setSelected(event.target.value as typeof selected)} className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"><option value="">{t('businessScenarios.activation.chooseRole')}</option>{available.map(row => <option key={row.id} value={row.id}>{row.title}</option>)}</select></label>
      <label htmlFor={`${id}-channel`} className="space-y-1 text-xs text-gray-600 dark:text-gray-300"><span>{t('businessScenarios.activation.channel')}</span><select id={`${id}-channel`} value={channel} onChange={event => setChannel(event.target.value as KnowledgeChannel)} className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800">{channels.map(value => <option key={value} value={value}>{t(`businessScenarios.activation.channels.${value}`)}</option>)}</select></label>
      <button type="button" disabled={!template} onClick={() => void save()} className="justify-self-start rounded-xl border border-blue-200 px-4 py-2 text-xs font-medium text-blue-600 disabled:opacity-50 dark:border-blue-800 dark:text-blue-300">{t('businessScenarios.activation.savePolicy')}</button>
    </fieldset>
  </section>;
}
