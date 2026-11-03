import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, ChevronDown, ChevronUp, ArrowRight, X } from 'lucide-react';
import { BUSINESS_SCENARIOS } from '@/utils/businessScenarios';
import type { BusinessScenario, ScenarioModule } from '@/types/businessScenarios';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import ScenarioResourcePreparation from './ScenarioResourcePreparation';
import ScenarioPreparationStatus from './ScenarioPreparationStatus';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';
import { useScenarioPreparationStore } from '@/stores/scenarioPreparationStore';
import { useAuthStore } from '@/stores/authStore';

interface Props {
  module: ScenarioModule;
  hasResources: boolean;
  onUse: (scenario: BusinessScenario) => void;
}

export default function ScenarioLibrary({ module, hasResources, onUse }: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(!hasResources);
  const manuallyToggled = useRef(false);
  useEffect(() => { if (!manuallyToggled.current) setExpanded(!hasResources); }, [hasResources]);
  const [example, setExample] = useState<BusinessScenario | null>(null);
  const dialogRef = useDialogFocus<HTMLElement>(() => setExample(null), Boolean(example));
  const scenes = BUSINESS_SCENARIOS.filter(scene => scene.module === module);
  const user = useAuthStore(state => state.user);
  const { catalog, channel, loading, failed, load: loadResources } = useScenarioResourceStore();
  const { entries, modules, load: loadPreparation, current } = useScenarioPreparationStore();
  useEffect(() => {
    if (!expanded) return;
    void loadResources(true);
    const refresh = () => { void loadResources(true); };
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, [expanded, loadResources, user?.id, user?.project_id, user?.role, channel]);
  useEffect(() => {
    if (expanded && catalog && !loading && !failed) void loadPreparation(module);
  }, [expanded, catalog, loading, failed, module, loadPreparation]);
  const retry = () => { void loadResources(true); };
  const available = (scene: BusinessScenario) => scene.available && current(module, scene.id)?.template.available !== false;
  return <section className="space-y-4" aria-label={t('businessScenarios.title')}>
    <div className="flex items-center justify-between gap-3">
      <div><h3 className="flex items-center gap-2 text-lg font-bold text-gray-900 dark:text-gray-100"><Lightbulb className="h-5 w-5 text-blue-600" />{t('businessScenarios.title')}</h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t('businessScenarios.subtitle')}</p></div>
      <button type="button" onClick={() => { manuallyToggled.current = true; setExpanded(!expanded); }} aria-expanded={expanded} className="flex shrink-0 items-center gap-1 rounded-xl px-3 py-2 text-sm text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20">
        {t(`businessScenarios.${expanded ? 'collapse' : 'expand'}`)}{expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
    </div>
    {expanded && <ScenarioResourcePreparation />}
    {expanded && <div className={`grid grid-cols-1 gap-4 md:grid-cols-2 ${module === 'agents' ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>{scenes.map(scene => <article key={scene.id} className="flex flex-col rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <h4 className="font-bold text-gray-900 dark:text-gray-100">{t(`businessScenarios.scenes.${scene.id}.title`)}</h4>
      <p className="mt-2 min-h-10 text-sm text-gray-500 dark:text-gray-400">{t(`businessScenarios.scenes.${scene.id}.benefit`)}</p>
      {module === 'workflows' && scene.id === 'logistics' && !available(scene) &&
        <p className="mt-2 text-xs leading-5 text-amber-700 dark:text-amber-400">{t('businessScenarios.orderWorkflowPending')}</p>}
      {module === 'workflows' && scene.id === 'aftersales' &&
        <p className="mt-2 text-xs leading-5 text-purple-700 dark:text-purple-300">{t('businessScenarios.aftersalesWorkflowHint')}</p>}
      <div className="mt-3 rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-900/50">
        <p className="font-medium text-gray-600 dark:text-gray-300">{t('businessScenarios.steps')}</p>
        <p className="mt-1 leading-5 text-gray-500 dark:text-gray-400">{t(`businessScenarios.scenes.${scene.id}.steps`)}</p>
      </div>
      <ScenarioPreparationStatus entry={loading || failed ? null : entries[`${module}:${scene.id}`] ?? null} failed={failed || modules[module]?.failed} onRetry={retry} />
      <div className="mt-auto flex items-center justify-between gap-2">
        <button onClick={() => setExample(scene)} className="rounded-lg py-2 text-sm text-gray-500 hover:text-blue-600">{t('businessScenarios.example')}</button>
        <button onClick={() => available(scene) ? onUse(scene) : setExample(scene)} className={`flex items-center gap-1 rounded-xl px-3 py-2 text-sm font-medium ${module === 'workflows' ? 'bg-purple-50 text-purple-600 hover:bg-purple-100 dark:bg-purple-900/30' : 'bg-blue-50 text-blue-600 hover:bg-blue-100 dark:bg-blue-900/30'}`}>
          {t(`businessScenarios.${!available(scene) ? 'planned' : module === 'devices' ? 'connect' : 'use'}`)}<ArrowRight size={14} />
        </button>
      </div>
    </article>)}</div>}
    {example && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setExample(null)}>
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="scenario-example-title" className="w-full max-w-xl rounded-3xl bg-white p-6 shadow-xl dark:bg-gray-900" onClick={event => event.stopPropagation()}>
        <div className="flex items-center justify-between"><h3 id="scenario-example-title" className="text-lg font-bold text-gray-900 dark:text-white">{t(`businessScenarios.scenes.${example.id}.title`)}</h3><button aria-label={t('businessScenarios.close')} onClick={() => setExample(null)} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><X size={20} /></button></div>
        <p className="my-5 whitespace-pre-line rounded-2xl bg-gray-50 p-4 text-sm leading-7 text-gray-700 dark:bg-gray-800 dark:text-gray-200">{t(`businessScenarios.scenes.${example.id}.example`)}</p>
        <p className="text-sm text-gray-600 dark:text-gray-300">{t(`businessScenarios.scenes.${example.id}.steps`)}</p>
        <p className="mt-4 text-xs text-gray-500">{t('businessScenarios.next')}</p>
        {available(example) && <button onClick={() => { onUse(example); setExample(null); }} className="mt-5 w-full rounded-xl bg-blue-600 py-3 font-medium text-white hover:bg-blue-700">{t('businessScenarios.configure')}</button>}
      </section>
    </div>}
  </section>;
}
