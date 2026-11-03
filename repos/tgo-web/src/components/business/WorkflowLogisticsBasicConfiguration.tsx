import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useBusinessConnectionStore } from '@/stores/businessConnectionStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import type { AnswerNodeData, BusinessQueryNodeData } from '@/types/workflow';
import type { GovernedLogisticsNodes, LogisticsNodeName } from '@/utils/businessScenarios';

const answerNames: LogisticsNodeName[] = [
  'answer_success', 'answer_order_pending',
  'answer_not_shipped', 'answer_logistics_pending',
];

export default function WorkflowLogisticsBasicConfiguration({ nodes, onAdvanced }: {
  nodes: GovernedLogisticsNodes;
  onAdvanced: () => void;
}) {
  const { t } = useTranslation();
  const { currentWorkflow, updateNode, setNodes, isExecuting, validationErrors } = useWorkflowStore();
  const { connections, loading, error, hasMore, load } = useBusinessConnectionStore();
  useEffect(() => { void useBusinessConnectionStore.getState().load(); }, []);

  const selectedId = (nodes.order.data as BusinessQueryNodeData).connection_id;
  const ready = connections.filter(connection => connection.enabled && connection.mode === 'http'
    && connection.test_status === 'passed'
    && connection.capabilities.includes('order_query')
    && connection.capabilities.includes('logistics_query'));
  const selected = connections.find(connection => connection.id === selectedId);
  const selectedReady = ready.some(connection => connection.id === selectedId);
  const fieldClass = 'mt-2 w-full rounded-xl border border-gray-200 bg-white p-3 text-sm text-gray-900 outline-none focus:border-purple-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white';

  return <div className="h-full overflow-auto p-4 md:p-8">
    <div className="mx-auto max-w-3xl space-y-5">
      {validationErrors.length > 0 && <ul role="alert" className="space-y-2 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-900 dark:bg-red-900/20">{validationErrors.map((item, index) => <li key={index}>{item.message}</li>)}</ul>}
      <div className="rounded-2xl border border-purple-100 bg-purple-50 p-4 text-sm leading-6 text-purple-700 dark:border-purple-900 dark:bg-purple-900/20 dark:text-purple-300">
        <h2 className="font-bold">{t('businessScenarios.workflow.logisticsBasic.title')}</h2>
        <p className="mt-1">{t('businessScenarios.workflow.logisticsBasic.steps')}</p>
        <p className="mt-2 text-xs">{t('businessScenarios.workflow.queryPrivateHint')}</p>
      </div>
      <section className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="font-bold text-gray-900 dark:text-white">{t('businessScenarios.workflow.logisticsBasic.inputTitle')}</h3>
        <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">{t('businessScenarios.workflow.logisticsBasic.inputHelp')}</p>
      </section>
      <section className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="font-bold text-gray-900 dark:text-white">{t('businessScenarios.workflow.logisticsBasic.queryTitle')}</h3>
        <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">{t('businessScenarios.workflow.logisticsBasic.queryHelp')}</p>
        <label className="mt-4 block text-sm font-medium text-gray-700 dark:text-gray-200">
          {t('businessScenarios.workflow.queryConnection')}
          <select value={selectedId} disabled={isExecuting || loading || Boolean(error)}
            onChange={event => {
              const current = useWorkflowStore.getState().currentWorkflow;
              if (!current || current.id !== currentWorkflow?.id) return;
              const queryIds = new Set([nodes.order.id, nodes.logistics.id]);
              setNodes(current.definition.nodes.map(node => node.type === 'business_query' && queryIds.has(node.id)
                ? { ...node, data: { ...node.data, connection_id: event.target.value } as BusinessQueryNodeData }
                : node));
            }} className={fieldClass}>
            <option value="">{t('businessScenarios.workflow.chooseConnection')}</option>
            {selectedId && !selectedReady && <option value={selectedId}>{selected?.name || t('businessScenarios.workflow.logisticsBasic.unknownConnection')}</option>}
            {ready.map(connection => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
          </select>
        </label>
        {loading && <p role="status" className="mt-2 text-xs text-gray-500">{t('businessScenarios.workflow.loadingConnections')}</p>}
        {error && <p role="alert" className="mt-2 text-xs text-red-600">{t('businessScenarios.workflow.connectionLoadFailed')}</p>}
        {hasMore && <button type="button" disabled={loading} onClick={() => void load(true)} className="mt-2 text-xs font-medium text-blue-600 disabled:opacity-50">{t('businessConnections.more')}</button>}
        {!loading && !error && !hasMore && !selectedReady && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{t('businessScenarios.workflow.logisticsBasic.connectionNeedsCheck')}</p>}
      </section>
      <section className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="font-bold text-gray-900 dark:text-white">{t('businessScenarios.workflow.logisticsBasic.branchTitle')}</h3>
        <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">{t('businessScenarios.workflow.logisticsBasic.branchHelp')}</p>
      </section>
      <section className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <h3 className="font-bold text-gray-900 dark:text-white">{t('businessScenarios.workflow.logisticsBasic.answersTitle')}</h3>
        <p className="mt-2 text-xs leading-5 text-gray-500">{t('businessScenarios.workflow.logisticsBasic.answersHelp')}</p>
        <div className="mt-4 space-y-4">{answerNames.map(name => {
          const node = nodes[name];
          const answer = node.data as AnswerNodeData;
          return <label key={name} className="block text-sm font-medium text-gray-700 dark:text-gray-200">
            {t(`businessScenarios.workflow.logisticsBasic.answers.${name}`)}
            <textarea value={answer.output_template || ''} maxLength={500} rows={3}
              disabled={isExecuting} onChange={event => updateNode(node.id, { output_template: event.target.value })}
              className={fieldClass} />
          </label>;
        })}</div>
      </section>
      <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 text-xs leading-5 text-blue-700 dark:border-blue-900 dark:bg-blue-900/20 dark:text-blue-300">
        <p>{t('businessScenarios.workflow.logisticsBasic.finishHelp')}</p>
        <button type="button" onClick={onAdvanced} className="mt-3 font-medium underline underline-offset-2">{t('businessScenarios.workflow.advanced')}</button>
      </div>
    </div>
  </div>;
}
