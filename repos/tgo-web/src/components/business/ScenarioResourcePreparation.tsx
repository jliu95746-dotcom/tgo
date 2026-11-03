import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';
import { useAuthStore } from '@/stores/authStore';
import type { KnowledgeChannel } from '@/types/scenarioResources';

const channels: KnowledgeChannel[] = ['web', 'wecom_kf', 'app', 'phone', 'internal'];

export default function ScenarioResourcePreparation() {
  const { t } = useTranslation();
  const project = useAuthStore(state => state.user?.project_id);
  const { catalog, channel, loading, failed, load, setChannel } = useScenarioResourceStore();
  useEffect(() => { void load(); }, [load, project, channel]);
  return <section className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h4 className="text-sm font-semibold text-gray-900 dark:text-white">{t('businessScenarios.preparation.title')}</h4><p className="mt-1 text-xs text-gray-500">{t('businessScenarios.preparation.description')}</p></div>
      <div className="flex items-center gap-2"><select value={channel} onChange={event => setChannel(event.target.value as KnowledgeChannel)} aria-label={t('businessScenarios.preparation.channel')} className="rounded-lg border border-gray-200 bg-gray-50 px-2 py-1.5 text-xs dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">{channels.map(value => <option key={value} value={value}>{t(`businessScenarios.preparation.channels.${value}`)}</option>)}</select><button type="button" onClick={() => void load(true)} disabled={loading} aria-label={t('common.refresh')} className="rounded-lg p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"><RefreshCw size={15} className={loading ? 'animate-spin' : ''} /></button></div>
    </div>
    {failed && <p role="alert" className="mt-3 text-xs text-red-600">{t('businessScenarios.preparation.failed')}</p>}
    {loading && !catalog && <p className="mt-3 text-xs text-gray-500">{t('businessScenarios.preparation.loading')}</p>}
    {catalog && <details className="mt-3"><summary className="cursor-pointer text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.preparation.overview')}</summary><div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{catalog.sections.map(section => {
      const count = section.items.filter(resource => resource.status === 'configured').length;
      return <div key={section.kind} className="rounded-xl bg-gray-50 px-3 py-2 text-xs dark:bg-gray-900"><div className="flex justify-between gap-2"><span className="text-gray-600 dark:text-gray-300">{t(`businessScenarios.preparation.kinds.${section.kind}`)}</span><span className={section.status === 'unavailable' ? 'text-red-600' : 'text-gray-500'}>{section.status === 'available' ? t('businessScenarios.preparation.configured', { count }) : t(`businessScenarios.preparation.sources.${section.status}`)}</span></div>{section.status === 'available' && section.items.length > count && <p className="mt-1 text-gray-500">{t('businessScenarios.preparation.needsAttention', { count: section.items.length - count })}</p>}</div>;
    })}</div></details>}
  </section>;
}
