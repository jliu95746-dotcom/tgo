import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { CreateAgentFormData } from '@/types';
import { formFromAgentDraft } from '@/utils/agentScenarioForm';
import { saveAgentSetupDraft } from '@/utils/agentSetupExperience';
import { useAuthStore } from '@/stores/authStore';
import { useScenarioDraftStore } from '@/stores/scenarioDraftStore';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';

interface Props {
  form: CreateAgentFormData;
  onResume: (form: Partial<CreateAgentFormData>) => void;
}

export default function ScenarioDraftControls({ form, onResume }: Props) {
  const { t } = useTranslation();
  const identity = useAuthStore(state => state.user);
  const store = useScenarioDraftStore();
  const [selection, setSelection] = useState('');
  const [message, setMessage] = useState('');
  const [expanded, setExpanded] = useState(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    void useScenarioDraftStore.getState().list('agents');
    return () => { mounted.current = false; };
  }, [identity?.id, identity?.project_id]);
  const current = store.current?.template_id === form.scenarioId ? store.current : null;
  const template = store.templates.find(value => value.module === 'agents' && value.id === form.scenarioId);
  useEffect(() => { if (current || store.drafts.length > 0) setExpanded(true); }, [current, store.drafts.length]);
  const save = async () => {
    if (!form.scenarioId || !template) return;
    setMessage('');
    try {
      const saved = await saveAgentSetupDraft(form);
      if (mounted.current && saved) setMessage(t('businessScenarios.drafts.saved'));
    } catch (error) {
      setMessage(t(error instanceof Error && error.message === 'toolConfig' ? 'businessScenarios.drafts.toolConfig' : 'businessScenarios.readiness.invalidModel'));
    }
  };
  const resume = async () => {
    setMessage('');
    const draft = await store.resume(selection);
    if (!draft || !mounted.current || draft.module !== 'agents') return;
    useScenarioResourceStore.getState().setChannel(draft.channel);
    onResume(formFromAgentDraft(draft));
    setMessage(t('businessScenarios.drafts.restored'));
  };
  return <section className="mb-4 rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-bold text-gray-800 dark:text-gray-100">{t('businessScenarios.drafts.title')}</h3>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="text-xs text-blue-600 dark:text-blue-300">{t(`businessScenarios.drafts.${expanded ? 'collapse' : 'expand'}`)}</button>
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button type="button" disabled={store.busy || !template || !form.scenarioId || identity?.role !== 'admin'} onClick={() => void save()} className="rounded-xl bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700 disabled:opacity-50 dark:bg-blue-900/30 dark:text-blue-300">{t('businessScenarios.drafts.save')}</button>
      {expanded && <>
      <select aria-label={t('businessScenarios.drafts.choose')} value={selection} onChange={event => setSelection(event.target.value)} className="min-w-0 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"><option value="">{t('businessScenarios.drafts.choose')}</option>{store.drafts.filter(draft => draft.module === 'agents').map(draft => <option key={draft.id} value={draft.id}>{draft.business.name || t(`businessScenarios.scenes.${draft.template_id}.title`)} · {t('businessScenarios.drafts.revision', { revision: draft.revision })}</option>)}</select>
      <button type="button" disabled={store.busy || !selection} onClick={() => void resume()} className="rounded-xl px-3 py-2 text-xs text-blue-600 disabled:opacity-50">{t('businessScenarios.drafts.resume')}</button>
      <button type="button" disabled={store.busy} onClick={() => void store.list('agents')} className="rounded-xl px-3 py-2 text-xs text-gray-500 disabled:opacity-50">{t('businessScenarios.drafts.refresh')}</button>
      </>}
    </div>
    {expanded && <p className="mt-2 text-xs leading-6 text-gray-500 dark:text-gray-400">{t('businessScenarios.drafts.description')}</p>}
    {store.busy && <p role="status" className="mt-2 text-xs text-gray-500">{t('businessScenarios.drafts.loading')}</p>}
    {current && <p className="mt-2 text-xs text-gray-500">{t('businessScenarios.drafts.current', { revision: current.revision, version: current.template_version })}</p>}
    {(message || store.failed) && <p role={store.failed ? 'alert' : 'status'} className={`mt-2 text-xs ${store.failed ? 'text-red-600' : 'text-gray-600 dark:text-gray-300'}`}>{store.failed ? t('businessScenarios.drafts.failed') : message}</p>}
  </section>;
}
