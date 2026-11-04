import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import type { ScenarioPreparationEntry } from '@/types/scenarioPreparation';
import { preparedResourceKinds } from '@/utils/scenarioPreparation';

interface Props {
  entry: ScenarioPreparationEntry | null;
  failed?: boolean;
  onRetry: () => void;
}

export default function ScenarioPreparationStatus({ entry, failed, onRetry }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  if (failed || entry?.status === 'failed') return <div role="alert" className="my-4 text-xs text-red-600 dark:text-red-400">
    <p>{t('businessScenarios.cardPreparation.failed')}</p>
    <button type="button" onClick={onRetry} className="mt-2 rounded-lg py-1 font-medium hover:underline">{t('businessScenarios.cardPreparation.retry')}</button>
  </div>;
  if (entry?.status === 'planned') return <p className="my-4 text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.cardPreparation.planned')}</p>;
  if (!entry?.report || entry.status === 'checking') return <p role="status" className="my-4 text-xs text-gray-500 dark:text-gray-400">{t('businessScenarios.cardPreparation.checking')}</p>;
  const { report, template } = entry;
  const prepared = preparedResourceKinds(template, report);
  return <div className="my-4 space-y-3 text-xs">
    <p className={report.configuration_ready ? 'text-emerald-700 dark:text-emerald-400' : 'font-medium text-amber-700 dark:text-amber-400'}>
      {t(`businessScenarios.cardPreparation.${report.configuration_ready ? 'prepared' : 'missing'}`, { count: report.issues.length })}
    </p>
    {report.issues.length > 0 && <ul className="space-y-2">{report.issues.map((issue, index) => <li key={`${issue.kind}:${index}`} className="rounded-xl bg-gray-50 p-3 dark:bg-gray-900">
      <p className="leading-5 text-gray-600 dark:text-gray-300">{issue.message}</p>
      <button type="button" onClick={() => issue.action === 'retry' ? onRetry() : navigate(issue.route)} className="mt-1 rounded-lg py-1 font-medium text-blue-600 hover:underline dark:text-blue-400">
        {t(`businessScenarios.readiness.actions.${issue.action}`)}
      </button>
    </li>)}</ul>}
    {prepared.length > 0 && <details className="text-gray-500 dark:text-gray-400">
      <summary className="cursor-pointer py-1">{t('businessScenarios.cardPreparation.readyResources', { count: prepared.length })}</summary>
      <ul className="mt-2 space-y-1">{prepared.map(kind => <li key={kind}>{t(`businessScenarios.preparation.kinds.${kind}`)} · {t('businessScenarios.cardPreparation.reused')}</li>)}</ul>
    </details>}
    <p className="leading-5 text-gray-500 dark:text-gray-400">{t('businessScenarios.cardPreparation.notTested')}</p>
  </div>;
}
