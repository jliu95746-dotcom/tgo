import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import { RefreshCw, ChevronDown } from 'lucide-react';
import { WorkflowApiService, WORKFLOW_EXECUTION_PAGE_SIZE, type WorkflowExecutionCursor } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import type { WorkflowSummary, WorkflowExecution, WorkflowCustomerMessageLookup, WorkflowChannelMessageLookup, NodeExecution } from '@/types/workflow';
import { isCustomerServiceChannelId } from '@/utils/workflowTraceNavigation';
import AIExecutionAttemptHistory from './AIExecutionAttemptHistory';
import AIExecutionDeviceSession from './AIExecutionDeviceSession';
import AIExecutionToolEventList from './AIExecutionToolEventList';
import WorkflowDeviceSessions from './WorkflowDeviceSessions';
import WorkflowBusinessQueryAudit from './WorkflowBusinessQueryAudit';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function WorkflowRunHistory({ workflows, initialChannelMessageLookup = null }: {
  workflows: WorkflowSummary[];
  initialChannelMessageLookup?: WorkflowChannelMessageLookup | null;
}) {
  const { t } = useTranslation();
  const isAdmin = useAuthStore(state => state.user?.role === 'admin');
  const currentProjectId = useAuthStore(state => state.user?.project_id);
  const [workflowId, setWorkflowId] = useState('');
  const [records, setRecords] = useState<WorkflowExecution[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [pageAnchor, setPageAnchor] = useState<WorkflowExecutionCursor | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [lookupInput, setLookupInput] = useState(initialChannelMessageLookup?.channel_id || '');
  const [activeBusinessRequestId, setActiveBusinessRequestId] = useState('');
  const [searchMode, setSearchMode] = useState<'business' | 'session' | 'message' | 'channelMessage' | 'parent' | 'execution'>(
    initialChannelMessageLookup ? 'channelMessage' : 'business',
  );
  const [activeVisitorSessionId, setActiveVisitorSessionId] = useState('');
  const [sourceMessageInput, setSourceMessageInput] = useState(initialChannelMessageLookup?.source_message_id || '');
  const [activeMessageLookup, setActiveMessageLookup] = useState<WorkflowCustomerMessageLookup | null>(null);
  const [activeChannelMessageLookup, setActiveChannelMessageLookup] = useState<WorkflowChannelMessageLookup | null>(initialChannelMessageLookup);
  const [activeParentExecutionId, setActiveParentExecutionId] = useState('');
  const [activeExecutionId, setActiveExecutionId] = useState('');
  useEffect(() => {
    setRecords([]);
    setHasMore(false);
    setPageAnchor(null);
    setActiveParentExecutionId('');
    setActiveExecutionId('');
  }, [currentProjectId]);
  useEffect(() => {
    let cancelled = false;
    if (pageAnchor === null) setRecords([]);
    setError(''); setLoading(Boolean(
      workflowId || (isAdmin && (activeExecutionId || activeParentExecutionId || activeChannelMessageLookup || activeMessageLookup || activeVisitorSessionId || activeBusinessRequestId)),
    ));
    const request = isAdmin && activeExecutionId
      ? WorkflowApiService.getExecution(activeExecutionId).then(record => [record])
      : isAdmin && activeParentExecutionId
      ? WorkflowApiService.listChildExecutions(activeParentExecutionId, 0, pageAnchor)
      : isAdmin && activeChannelMessageLookup
      ? WorkflowApiService.listChannelMessageExecutions(activeChannelMessageLookup, 0, pageAnchor)
      : isAdmin && activeMessageLookup
        ? WorkflowApiService.listCustomerMessageExecutions(activeMessageLookup, 0, pageAnchor)
        : isAdmin && activeVisitorSessionId
        ? WorkflowApiService.listVisitorSessionExecutions(activeVisitorSessionId, 0, pageAnchor)
        : isAdmin && activeBusinessRequestId
          ? WorkflowApiService.listBusinessRequestExecutions(activeBusinessRequestId, 0, pageAnchor)
          : workflowId ? WorkflowApiService.listExecutions(workflowId, 0, pageAnchor) : null;
    if (request) request.then(data => { if (!cancelled) {
      setRecords(previous => {
        if (pageAnchor === null) return data;
        const knownIds = new Set(previous.map(record => record.id));
        return [...previous, ...data.filter(record => !knownIds.has(record.id))];
      });
      setHasMore(data.length === WORKFLOW_EXECUTION_PAGE_SIZE);
    } })
      .catch(() => { if (!cancelled) setError(t('businessScenarios.workflow.failedLoad')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [activeBusinessRequestId, activeVisitorSessionId, activeMessageLookup, activeChannelMessageLookup, activeParentExecutionId, activeExecutionId, currentProjectId, isAdmin, workflowId, refresh, pageAnchor, t]);
  const searchOrigin = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const lookupId = lookupInput.trim();
    if (searchMode === 'channelMessage'
      ? !isCustomerServiceChannelId(lookupId)
      : !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(lookupId)) {
      setError(t(`businessScenarios.workflow.runTrace.${searchMode === 'channelMessage' ? 'invalidChannel' : searchMode === 'execution' ? 'invalidExecution' : searchMode === 'parent' ? 'invalidParent' : searchMode === 'business' ? 'invalidRequest' : 'invalidSession'}`));
      return;
    }
    const sourceMessageId = sourceMessageInput.trim();
    if ((searchMode === 'message' || searchMode === 'channelMessage') && (!sourceMessageId || sourceMessageId.length > 255)) {
      setError(t('businessScenarios.workflow.runTrace.invalidMessage'));
      return;
    }
    setWorkflowId('');
    setActiveBusinessRequestId(searchMode === 'business' ? lookupId : '');
    setActiveParentExecutionId(searchMode === 'parent' ? lookupId : '');
    setActiveExecutionId(searchMode === 'execution' ? lookupId : '');
    setActiveVisitorSessionId(searchMode === 'session' ? lookupId : '');
    setActiveMessageLookup(searchMode === 'message' ? {
      visitor_session_id: lookupId, source_message_id: sourceMessageId,
    } : null);
    setActiveChannelMessageLookup(searchMode === 'channelMessage' ? {
      channel_id: lookupId, source_message_id: sourceMessageId,
    } : null);
    setPageAnchor(null);
    setHasMore(false);
    setRefresh(value => value + 1);
  };
  const showChildRuns = (parentExecutionId: string) => {
    setSearchMode('parent');
    setLookupInput(parentExecutionId);
    setWorkflowId('');
    setActiveBusinessRequestId('');
    setActiveVisitorSessionId('');
    setActiveMessageLookup(null);
    setActiveChannelMessageLookup(null);
    setActiveParentExecutionId(parentExecutionId);
    setActiveExecutionId('');
    setPageAnchor(null);
    setHasMore(false);
    setRefresh(value => value + 1);
  };
  const showExecution = (executionId: string) => {
    setSearchMode('execution');
    setLookupInput(executionId);
    setWorkflowId('');
    setActiveBusinessRequestId('');
    setActiveVisitorSessionId('');
    setActiveMessageLookup(null);
    setActiveChannelMessageLookup(null);
    setActiveParentExecutionId('');
    setActiveExecutionId(executionId);
    setPageAnchor(null);
    setHasMore(false);
    setRefresh(value => value + 1);
  };
  return <section className="space-y-4">
    <div className="flex gap-3"><select aria-label={t('businessScenarios.workflow.chooseWorkflow')} value={workflowId} onChange={event => { setActiveBusinessRequestId(''); setActiveVisitorSessionId(''); setActiveMessageLookup(null); setActiveChannelMessageLookup(null); setActiveParentExecutionId(''); setActiveExecutionId(''); setPageAnchor(null); setHasMore(false); setWorkflowId(event.target.value); }} className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"><option value="">{t('businessScenarios.workflow.chooseWorkflow')}</option>{workflows.map(workflow => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}</select><button disabled={loading || !(workflowId || activeBusinessRequestId || activeVisitorSessionId || activeMessageLookup || activeChannelMessageLookup || activeParentExecutionId || activeExecutionId)} aria-label={t('common.refresh')} onClick={() => { setPageAnchor(null); setRefresh(value => value + 1); }} className="rounded-xl border border-gray-200 p-3 text-gray-500 dark:border-gray-700"><RefreshCw size={18} className={loading ? 'animate-spin' : ''} /></button></div>
    {isAdmin && <form onSubmit={searchOrigin} className="flex flex-wrap items-end gap-3">
      <label className="flex min-w-40 flex-col gap-2 text-sm text-gray-600 dark:text-gray-300">
        {t('businessScenarios.workflow.runTrace.searchType')}
        <select value={searchMode} onChange={event => setSearchMode(event.target.value as typeof searchMode)} className="rounded-xl border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white">
          <option value="business">{t('businessScenarios.workflow.runTrace.searchByRequest')}</option>
          <option value="parent">{t('businessScenarios.workflow.runTrace.searchByParent')}</option>
          <option value="execution">{t('businessScenarios.workflow.runTrace.searchByExecution')}</option>
          <option value="session">{t('businessScenarios.workflow.runTrace.searchBySession')}</option>
          <option value="message">{t('businessScenarios.workflow.runTrace.searchByMessage')}</option>
          <option value="channelMessage">{t('businessScenarios.workflow.runTrace.searchByChannelMessage')}</option>
        </select>
      </label>
      <label className="flex min-w-56 flex-1 flex-col gap-2 text-sm text-gray-600 dark:text-gray-300">
        {t('businessScenarios.workflow.runTrace.searchLabel')}
        <input value={lookupInput} onChange={event => setLookupInput(event.target.value)} placeholder={t(`businessScenarios.workflow.runTrace.${searchMode === 'business' ? 'searchPlaceholder' : searchMode === 'parent' ? 'parentPlaceholder' : searchMode === 'execution' ? 'executionPlaceholder' : searchMode === 'channelMessage' ? 'channelPlaceholder' : 'sessionPlaceholder'}`)} className="rounded-xl border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white" />
      </label>
      {(searchMode === 'message' || searchMode === 'channelMessage') && <label className="flex min-w-56 flex-1 flex-col gap-2 text-sm text-gray-600 dark:text-gray-300">
        {t('businessScenarios.workflow.runTrace.sourceMessage')}
        <input value={sourceMessageInput} onChange={event => setSourceMessageInput(event.target.value)} placeholder={t('businessScenarios.workflow.runTrace.sourceMessagePlaceholder')} className="rounded-xl border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white" />
      </label>}
      <button type="submit" disabled={loading} className="rounded-xl bg-blue-600 px-4 py-3 text-sm text-white disabled:opacity-50">{t('businessScenarios.workflow.runTrace.search')}</button>
    </form>}
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {isAdmin && activeChannelMessageLookup && <AIExecutionAttemptHistory
      key={JSON.stringify([
        currentProjectId, activeChannelMessageLookup.channel_id,
        activeChannelMessageLookup.source_message_id, refresh,
      ])}
      lookup={activeChannelMessageLookup}
    />}
    {(workflowId || (isAdmin && (activeBusinessRequestId || activeVisitorSessionId || activeMessageLookup || activeChannelMessageLookup || activeParentExecutionId || activeExecutionId))) && !loading && !error && !records.length && <p className="rounded-2xl bg-white p-8 text-center text-sm text-gray-500 dark:bg-gray-800">{t(activeParentExecutionId ? 'businessScenarios.workflow.runTrace.noChildRuns' : activeMessageLookup || activeChannelMessageLookup ? 'businessScenarios.workflow.runTrace.noRelatedMessageRun' : 'businessScenarios.workflow.empty')}</p>}
    {records.map(record => <details key={record.id} className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
      <summary className="flex cursor-pointer items-center justify-between gap-3 text-sm text-gray-700 dark:text-gray-200"><span>{new Date(record.started_at).toLocaleString()} · {t(`workflow.execution.status.${record.status}`, record.status)}</span><ChevronDown size={16} /></summary>
      <p className="mt-3 break-all text-xs text-gray-400">{record.id}</p>
      {(activeBusinessRequestId || activeVisitorSessionId || activeMessageLookup || activeChannelMessageLookup || activeParentExecutionId || activeExecutionId) && <p className="mt-1 text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.workflow')}：{workflows.find(workflow => workflow.id === record.workflow_id)?.name || record.workflow_id}</p>}
      <p className="mt-2 text-xs text-gray-500">{t(`businessScenarios.workflow.mode.${record.execution_mode || 'unknown'}`)}{record.workflow_version != null ? ` · v${record.workflow_version}` : ''}</p>
      {record.release_id && <p className="mt-2 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.release')}：{record.release_id}</p>}
      {record.employee_agent_id && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.employee')}：{record.employee_agent_id}</p>}
      {record.definition_digest && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.digest')}：{record.definition_digest}</p>}
      {record.ai_request_id ? <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.aiRequest')}：{record.ai_request_id}</p> : <p className="mt-1 text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.unknown')}</p>}
      {record.parent_execution_id && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.parentExecution')}：{record.parent_execution_id}{isAdmin && <button type="button" onClick={() => showExecution(record.parent_execution_id!)} className="ml-2 text-blue-600 hover:underline dark:text-blue-300">{t('businessScenarios.workflow.runTrace.viewParentRun')}</button>}</p>}
      {record.workflow_lineage && record.workflow_lineage.length > 1 && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.callChain')}：{record.workflow_lineage.join(' → ')}</p>}
      {isAdmin && <button type="button" onClick={() => showChildRuns(record.id)} className="mt-2 text-xs text-blue-600 hover:underline dark:text-blue-300">{t('businessScenarios.workflow.runTrace.viewChildRuns')}</button>}
      {record.business_request_id && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.businessRequest')}：{record.business_request_id}</p>}
      {record.visitor_session_id && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.visitorSession')}：{record.visitor_session_id}</p>}
      {record.source_message_id && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.sourceMessage')}：{record.source_message_id}</p>}
      {record.source_channel && <p className="mt-1 break-all text-xs text-gray-500">{t('businessScenarios.workflow.runTrace.sourceChannel')}：{record.source_channel === 'internal_event' ? t('workflowEvents.source') : t(`businessScenarios.activation.channels.${record.source_channel}`, record.source_channel)}</p>}
      {record.error && <p className="mt-3 whitespace-pre-wrap text-sm text-red-600">{record.error}</p>}
      {typeof record.output === 'string' ? (
        <div className="mt-3 max-h-72 overflow-auto rounded-xl bg-gray-50 p-3 text-sm leading-6 text-gray-700 dark:bg-gray-900 dark:text-gray-300 [&_p+p]:mt-3 [&_strong]:font-semibold">
          <ReactMarkdown>{record.output}</ReactMarkdown>
        </div>
      ) : (
        <pre className="mt-3 max-h-72 overflow-auto rounded-xl bg-gray-50 p-3 text-xs text-gray-600 dark:bg-gray-900 dark:text-gray-300">{JSON.stringify(record.output ?? null, null, 2)}</pre>
      )}
      {record.node_executions?.map(node => <WorkflowNodeExecutionReceipt key={node.id} node={node} isAdmin={isAdmin} />)}
      <WorkflowDeviceSessions workflowExecutionId={record.id} projectId={currentProjectId} />
    </details>)}
    {hasMore && !error && <button type="button" disabled={loading} onClick={() => { const last = records[records.length - 1]; if (last) setPageAnchor({ id: last.id, started_at: last.started_at }); }} className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm text-blue-600 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-blue-300">{t('businessScenarios.workflow.runTrace.loadMore')}</button>}
  </section>;
}

function WorkflowNodeExecutionReceipt({ node, isAdmin }: { node: NodeExecution; isAdmin: boolean }) {
  const { t } = useTranslation();
  const output = node.output && typeof node.output === 'object' && !Array.isArray(node.output)
    ? node.output as Record<string, unknown> : null;
  const conditionResult = node.node_type === 'condition' && typeof output?.result === 'boolean'
    ? output.result : null;
  const apiStatusCode = node.node_type === 'api' && typeof output?.status_code === 'number'
    && Number.isInteger(output.status_code) && output.status_code >= 100 && output.status_code <= 599
    ? output.status_code : null;
  const approvalRequested = node.node_type === 'approval' && output?.requested === true;
  const startedAt = recordedTime(node.started_at);
  const completedAt = recordedTime(node.completed_at);
  return <div className="mt-2 rounded-xl border border-gray-100 bg-gray-50/60 p-3 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-300">
    <p className="font-medium">{t(`workflow.node_types.${node.node_type}.label`, node.node_type)} · {t(`workflow.execution.status.${node.status}`, node.status)}</p>
    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-gray-500 dark:text-gray-400">
      {startedAt && <span>{t('businessScenarios.workflow.stepEvidence.startedAt')}：{startedAt}</span>}
      {completedAt && <span>{t('businessScenarios.workflow.stepEvidence.completedAt')}：{completedAt}</span>}
      {typeof node.duration === 'number' && Number.isFinite(node.duration) && node.duration >= 0
        && <span>{t('businessScenarios.workflow.stepEvidence.duration')}：{node.duration} ms</span>}
    </div>
    {conditionResult !== null && <p className="mt-1">{t(`businessScenarios.workflow.stepEvidence.${conditionResult ? 'conditionMatched' : 'conditionNotMatched'}`)}</p>}
    {apiStatusCode !== null && <p className="mt-1">{t('businessScenarios.workflow.stepEvidence.httpStatus')}：{apiStatusCode}</p>}
    {approvalRequested && <p className="mt-1">{t('businessScenarios.workflow.stepEvidence.approvalRequested')}</p>}
    {output?.capture_status === 'redacted' && <p className="mt-1 text-gray-400">{t('businessScenarios.workflow.stepEvidence.detailsRedacted')}</p>}
    {node.error && <p className="mt-1 text-red-600 dark:text-red-400">{node.error}</p>}
    {isAdmin && node.node_type === 'agent' && <WorkflowAgentExecutionReceipt output={node.output} />}
    {isAdmin && node.node_type === 'business_query' && <WorkflowBusinessQueryReceipt output={node.output} />}
  </div>;
}

function recordedTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? null : time.toLocaleString();
}

function WorkflowAgentExecutionReceipt({ output }: { output: unknown }) {
  const { t } = useTranslation();
  const [showToolEvents, setShowToolEvents] = useState(false);
  const [showDeviceActions, setShowDeviceActions] = useState(false);
  if (!output || typeof output !== 'object' || Array.isArray(output)) return null;
  const executionId = (output as Record<string, unknown>).agent_execution_id;
  if (typeof executionId !== 'string' || !UUID_PATTERN.test(executionId)) return null;

  return <div className="mt-1 border-l-2 border-purple-200 pl-3 dark:border-purple-800">
    <p className="break-all text-gray-600 dark:text-gray-300">{t('businessScenarios.workflow.runTrace.childAiExecution')}：{executionId}</p>
    <button type="button" onClick={() => setShowToolEvents(value => !value)} className="mt-1 text-blue-600 hover:underline dark:text-blue-300">
      {t(`businessScenarios.workflow.toolEvents.${showToolEvents ? 'hide' : 'show'}`)}
    </button>
    {showToolEvents && <AIExecutionToolEventList executionId={executionId} />}
    <button type="button" onClick={() => setShowDeviceActions(value => !value)} className="ml-4 mt-1 text-blue-600 hover:underline dark:text-blue-300">
      {t(`businessScenarios.workflow.deviceExecution.${showDeviceActions ? 'hide' : 'show'}`)}
    </button>
    {showDeviceActions && <AIExecutionDeviceSession executionId={executionId} />}
  </div>;
}

function WorkflowBusinessQueryReceipt({ output }: { output: unknown }) {
  const { t } = useTranslation();
  if (!output || typeof output !== 'object' || Array.isArray(output)) return null;
  const evidence = output as Record<string, unknown>;
  const queryId = evidence.query_id;
  const connectionId = evidence.connection_id;
  const operation = evidence.operation;
  if (typeof queryId !== 'string' || !UUID_PATTERN.test(queryId)
    || typeof connectionId !== 'string' || !UUID_PATTERN.test(connectionId)
    || (operation !== 'order_query' && operation !== 'logistics_query')) return null;

  return <div className="mt-1 space-y-1 border-l-2 border-blue-200 pl-3 text-gray-600 dark:border-blue-800 dark:text-gray-300">
    <p>{t('businessScenarios.workflow.runTrace.queryOperation')}：{t(`businessScenarios.workflow.queryOperations.${operation}`)}</p>
    <p className="break-all">{t('businessScenarios.workflow.runTrace.businessQuery')}：{queryId}</p>
    <p className="break-all">{t('businessScenarios.workflow.runTrace.businessConnection')}：{connectionId}</p>
    <WorkflowBusinessQueryAudit queryId={queryId} />
  </div>;
}
