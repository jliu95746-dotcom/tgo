import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useBusinessConnectionStore } from '@/stores/businessConnectionStore';
import type { BusinessQueryNodeData } from '@/types/workflow';

export default function BusinessQueryNodeConfig({ data, onUpdate }: {
  data: BusinessQueryNodeData;
  onUpdate: (updates: Partial<BusinessQueryNodeData>) => void;
}) {
  const { t } = useTranslation();
  const connections = useBusinessConnectionStore(state => state.connections);
  const loading = useBusinessConnectionStore(state => state.loading);
  const error = useBusinessConnectionStore(state => state.error);
  useEffect(() => { void useBusinessConnectionStore.getState().load(); }, []);
  const ready = connections.filter(item => item.enabled && item.mode === 'http'
    && item.test_status === 'passed' && item.capabilities.includes(data.operation));
  const current = connections.find(item => item.id === data.connection_id);
  return <div className="space-y-5 text-sm text-gray-700 dark:text-gray-200">
    <p className="rounded-xl bg-blue-50 p-3 text-xs leading-5 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{t('businessScenarios.workflow.queryPrivateHint')}</p>
    <label className="block space-y-2">
      <span className="font-medium">{t('businessScenarios.workflow.queryOperation')}</span>
      <select value={data.operation} onChange={event => onUpdate({ operation: event.target.value as BusinessQueryNodeData['operation'], connection_id: '' })} className="w-full rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
        <option value="order_query">{t('businessScenarios.workflow.queryOperations.order_query')}</option>
        <option value="logistics_query">{t('businessScenarios.workflow.queryOperations.logistics_query')}</option>
      </select>
    </label>
    <label className="block space-y-2">
      <span className="font-medium">{t('businessScenarios.workflow.queryConnection')}</span>
      <select value={data.connection_id} onChange={event => onUpdate({ connection_id: event.target.value })} className="w-full rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
        <option value="">{t('businessScenarios.workflow.chooseConnection')}</option>
        {current && !ready.some(item => item.id === current.id) && <option value={current.id}>{current.name}</option>}
        {ready.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </label>
    {loading && <p className="text-xs text-gray-500">{t('businessScenarios.workflow.loadingConnections')}</p>}
    {error && <p role="alert" className="text-xs text-red-600">{t('businessScenarios.workflow.connectionLoadFailed')}</p>}
    {!loading && !error && ready.length === 0 && <p className="text-xs text-amber-700 dark:text-amber-300">{t('businessScenarios.workflow.noReadyConnection')}</p>}
  </div>;
}
