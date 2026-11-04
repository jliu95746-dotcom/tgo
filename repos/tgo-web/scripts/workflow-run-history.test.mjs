import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function renderHistory(record, activeRequest = '', role = 'admin', sessionRequest = '', messageLookup = null, channelMessageLookup = null, parentId = '', executionId = '') {
  const effects = [];
  const requests = [];
  const react = {
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
    useEffect: effect => { effects.push(effect); },
    useState: initial => [states.shift() ?? initial, () => {}],
  };
  const states = [channelMessageLookup ? '' : 'flow-a', record ? [record] : [], false, '', 0, 0, false,
    channelMessageLookup?.channel_id || messageLookup?.visitor_session_id || sessionRequest || activeRequest || parentId || executionId,
    activeRequest, executionId ? 'execution' : parentId ? 'parent' : channelMessageLookup ? 'channelMessage' : messageLookup ? 'message' : sessionRequest ? 'session' : 'business',
    sessionRequest, channelMessageLookup?.source_message_id || messageLookup?.source_message_id || '',
    messageLookup, channelMessageLookup, parentId, executionId];
  const exports = {};
  const source = readFileSync(new URL('../src/components/business/WorkflowRunHistory.tsx', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText;
  vm.runInNewContext(output, {
    exports, React: react,
    require: name => {
      if (name === 'react') return react;
      if (name === 'react-i18next') return { useTranslation: () => ({ t: (_key, fallback) => fallback ?? _key }) };
      if (name === 'lucide-react') return { RefreshCw: 'RefreshCw', ChevronDown: 'ChevronDown' };
      if (name === 'react-markdown') return function ReactMarkdown() {};
      if (['./AIExecutionAttemptHistory', './AIExecutionDeviceSession',
        './AIExecutionToolEventList', './WorkflowDeviceSessions',
        './WorkflowBusinessQueryAudit'].includes(name)) return { default: function ChildRunDetails() {} };
      if (name === '@/services/workflowApi') return { WorkflowApiService: {
        listExecutions: id => { requests.push(['workflow', id]); return Promise.resolve([]); },
        listBusinessRequestExecutions: id => { requests.push(['business', id]); return Promise.resolve([]); },
        listVisitorSessionExecutions: id => { requests.push(['session', id]); return Promise.resolve([]); },
        listCustomerMessageExecutions: request => { requests.push(['message', request]); return Promise.resolve([]); },
        listChannelMessageExecutions: request => { requests.push(['channel-message', request]); return Promise.resolve([]); },
        listChildExecutions: id => { requests.push(['parent', id]); return Promise.resolve([]); },
        getExecution: id => { requests.push(['execution', id]); return Promise.resolve(record); },
      } };
      if (name === '@/utils/workflowTraceNavigation') return {
        isCustomerServiceChannelId: value => value.endsWith('-vtr'),
      };
      if (name === '@/stores/authStore') return { useAuthStore: selector => selector({ user: { role } }) };
      throw Error(`Unexpected dependency ${name}`);
    },
  });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child => Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  const nodes = flatten(exports.default({
    workflows: [{ id: 'flow-a', name: '商品咨询客服' }],
    initialChannelMessageLookup: channelMessageLookup,
  }));
  return Object.assign(nodes, { effects, requests });
}

test('history renders a completed text answer without JSON escapes', () => {
  const answer = '**结论：不建议。**\n\n请查看商品容量。';
  const nodes = renderHistory({ id: 'run-a', status: 'completed', output: answer, started_at: '2026-10-14T00:00:00Z' });
  const markdown = nodes.find(node => node?.type?.name === 'ReactMarkdown');
  assert.ok(markdown?.children.includes(answer));
  assert.ok(!nodes.some(node => node?.type === 'pre' && node.children.includes(JSON.stringify(answer))));
});

test('history keeps structured output inspectable and a failed run visibly failed', () => {
  const nodes = renderHistory({ id: 'run-b', status: 'failed', output: { step: 'query' }, error: '查询失败', started_at: '2026-10-14T00:00:00Z' });
  assert.ok(nodes.some(node => node?.type === 'pre' && node.children.includes(JSON.stringify({ step: 'query' }, null, 2))));
  assert.ok(nodes.some(node => node?.children?.includes('查询失败')));
  assert.ok(nodes.some(node => node?.children?.some?.(child => typeof child === 'string' && child.includes('failed'))));
});

test('history shows each step timing and condition result without revealing raw input', () => {
  const nodes = renderHistory({
    id: 'run-steps', status: 'completed', started_at: '2026-10-14T00:00:00Z',
    node_executions: [{
      id: 'step-1', node_id: 'condition-1', node_type: 'condition',
      status: 'completed', started_at: '2026-10-14T00:00:01Z',
      completed_at: '2026-10-14T00:00:02Z', duration: 35,
      input: { private_order_no: 'ORDER-PRIVATE-1234' }, output: { result: false },
    }],
  });
  const step = nodes.find(node => node?.type?.name === 'WorkflowNodeExecutionReceipt');
  assert.ok(step);
  const flattenStep = node => [node, ...(node?.children ?? []).flatMap(child =>
    Array.isArray(child) ? child.flatMap(flattenStep) : flattenStep(child))];
  const rendered = flattenStep(step.type(step.props));
  const visible = rendered.flatMap(node => node?.children ?? [])
    .filter(value => typeof value === 'string' || typeof value === 'number')
    .join('');
  assert.ok(visible.includes('35 ms'));
  assert.ok(visible.includes('conditionNotMatched'));
  assert.ok(visible.includes('startedAt'));
  assert.ok(visible.includes('completedAt'));
  assert.ok(!JSON.stringify(rendered).includes('ORDER-PRIVATE-1234'));
});

test('history shows safe API and approval outcomes without provider payload', () => {
  const nodes = renderHistory({
    id: 'run-outcomes', status: 'completed', started_at: '2026-10-14T00:00:00Z',
    node_executions: [
      { id: 'api-1', node_id: 'api-1', node_type: 'api', status: 'completed',
        output: { capture_status: 'redacted', status_code: 204, private_body: 'PROVIDER-SECRET' } },
      { id: 'approval-1', node_id: 'approval-1', node_type: 'approval', status: 'completed',
        output: { requested: true } },
    ],
  });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child =>
    Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  const receipts = nodes.filter(node => node?.type?.name === 'WorkflowNodeExecutionReceipt')
    .flatMap(node => flatten(node.type(node.props)));
  const visible = receipts.flatMap(node => node?.children ?? [])
    .filter(value => typeof value === 'string' || typeof value === 'number')
    .join('');
  assert.ok(visible.includes('httpStatus'));
  assert.ok(visible.includes('204'));
  assert.ok(visible.includes('approvalRequested'));
  assert.ok(!JSON.stringify(receipts).includes('PROVIDER-SECRET'));
});

test('administrator can search workflow history by business request', () => {
  const requestId = '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833';
  const nodes = renderHistory({ id: 'run-c', status: 'completed', started_at: '2026-10-14T00:00:00Z' }, requestId);
  assert.ok(nodes.some(node => node?.type === 'form'), 'expected a request lookup form');
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === requestId));
  assert.ok(nodes.some(node => node?.children?.some?.(child => typeof child === 'string' && child.includes('商品咨询客服'))));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['business', requestId]]);
});

test('non-administrator cannot start a business request lookup from history', () => {
  const requestId = '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833';
  const nodes = renderHistory({ id: 'run-d', status: 'completed', started_at: '2026-10-14T00:00:00Z' }, requestId, 'agent');
  assert.ok(!nodes.some(node => node?.type === 'form'));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['workflow', 'flow-a']]);
});

test('administrator can find related workflows from a verified customer session', () => {
  const sessionId = '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833';
  const nodes = renderHistory({ id: 'run-e', status: 'completed', started_at: '2026-10-14T00:00:00Z' }, '', 'admin', sessionId);
  assert.ok(nodes.some(node => node?.type === 'select' && node.props.value === 'session'));
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === sessionId));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['session', sessionId]]);
});

test('customer message lookup requires both the session and original message ID', () => {
  const lookup = {
    visitor_session_id: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833',
    source_message_id: 'original-message-42',
  };
  const nodes = renderHistory({ id: 'run-f', status: 'completed', started_at: '2026-10-14T00:00:00Z' }, '', 'admin', '', lookup);
  assert.ok(nodes.some(node => node?.type === 'select' && node.props.value === 'message'));
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === lookup.visitor_session_id));
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === lookup.source_message_id));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['message', lookup]]);
});

test('empty customer message lookup explains that no linked workflow may have run', () => {
  const lookup = {
    visitor_session_id: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833',
    source_message_id: 'original-message-42',
  };
  const nodes = renderHistory(null, '', 'admin', '', lookup);
  assert.ok(nodes.some(node => node?.type === 'p' && node.children.includes('businessScenarios.workflow.runTrace.noRelatedMessageRun')));
});

test('chat navigation opens the verified channel message in run history', () => {
  const lookup = {
    channel_id: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833-vtr',
    source_message_id: 'original-message-42',
  };
  const nodes = renderHistory(null, '', 'admin', '', null, lookup);
  assert.ok(nodes.some(node => node?.type === 'select' && node.props.value === 'channelMessage'));
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === lookup.channel_id));
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === lookup.source_message_id));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['channel-message', lookup]]);
  assert.ok(nodes.some(node => node?.type === 'p'
    && node.children.includes('businessScenarios.workflow.runTrace.noRelatedMessageRun')));
});

test('administrator can inspect child runs from an owned parent execution', () => {
  const parentId = '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833';
  const nodes = renderHistory({
    id: 'child-run', parent_execution_id: parentId,
    status: 'completed', started_at: '2026-10-14T00:00:00Z',
  }, '', 'admin', '', null, null, parentId);
  assert.ok(nodes.some(node => node?.type === 'select' && node.props.value === 'parent'));
  assert.ok(nodes.some(node => node?.type === 'input' && node.props.value === parentId));
  assert.ok(nodes.some(node => node?.type === 'button'
    && node.children.includes('businessScenarios.workflow.runTrace.viewChildRuns')));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['parent', parentId]]);
});

test('administrator can open the exact parent run from a child record', () => {
  const parentId = '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833';
  const nodes = renderHistory({
    id: 'child-run', parent_execution_id: parentId,
    status: 'completed', started_at: '2026-10-14T00:00:00Z',
  }, '', 'admin', '', null, null, '', parentId);
  assert.ok(nodes.some(node => node?.type === 'button'
    && node.children.includes('businessScenarios.workflow.runTrace.viewParentRun')));
  nodes.effects[1]();
  assert.deepEqual(nodes.requests, [['execution', parentId]]);
});
