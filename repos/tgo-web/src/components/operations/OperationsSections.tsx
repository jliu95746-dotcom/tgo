import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import OperationsBilling from '../settings/OperationsBilling';
import OperationsTrialPolicy from '../settings/OperationsTrialPolicy';
import OperationsTrialCodes from '../settings/OperationsTrialCodes';
import OperationsHealth from '../settings/OperationsHealth';
import OperationsTasks from '../settings/OperationsTasks';
import OperationsSupport from '../settings/OperationsSupport';
import CommercialReadiness from '../settings/CommercialReadiness';
import { opsButton } from './ui';

export default function OperationsSections({ kind }: { kind: 'plans' | 'monitor' | 'finance' }) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  if (kind === 'finance') {
    const initialTab = (['orders', 'refunds', 'invoices', 'reconciliation', 'quotaReview'] as const).find(item => item === params.get('tab')) || 'orders';
    return <OperationsSupport key={initialTab} initialTab={initialTab} />;
  }
  const tabs = kind === 'plans' ? ['catalogue', 'policy', 'codes'] : ['health', 'tasks', 'readiness'];
  const tab = tabs.find(item => item === params.get('tab')) || tabs[0];
  const namespace = kind === 'plans' ? 'planTabs' : 'monitorTabs';
  return <div className="space-y-5"><div role="group" className="flex flex-wrap gap-2">{tabs.map(item => <button key={item} aria-pressed={tab === item} className={`${opsButton} ${tab === item ? '!border-indigo-200 !bg-indigo-50 !text-indigo-700' : ''}`} onClick={() => setParams({ tab: item })}>{t(`opsWorkspace.${namespace}.${item}`)}</button>)}</div>
    {tab === 'catalogue' && <OperationsBilling />}{tab === 'policy' && <OperationsTrialPolicy />}{tab === 'codes' && <OperationsTrialCodes />}{tab === 'health' && <OperationsHealth />}{tab === 'tasks' && <OperationsTasks tasksOnly />}{tab === 'readiness' && <CommercialReadiness />}
  </div>;
}
