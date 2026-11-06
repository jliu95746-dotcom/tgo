import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

const copy = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/i18n/businessScenarios.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: copy });
const exports = {};
const variables = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/workflowVariables.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: variables });
const user = { id: 'staff-a', project_id: 'project-a', role: 'admin', username: 'tester' };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/workflow/panels/ApprovalNodeConfig.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText, { exports, require: name => {
  if (name === 'react') return React;
  if (name === 'react/jsx-runtime') return jsxRuntime;
  if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key.split('.').slice(1).reduce((value, part) => value[part], copy.businessScenariosZh) }) };
  if (name === '@/services/staffApi') return { staffApi: { listStaff: () => { throw Error('server rendering must not fetch staff'); } } };
  if (name === '@/stores/authStore') return { useAuthStore: selector => selector({ user }) };
  if (name === '@/utils/workflowVariables') return variables;
  throw Error(`Unexpected dependency: ${name}`);
} });

function render(kinds, data = {}, definition = {}) {
  const updates = [];
  const nodes = definition.nodes ?? kinds.map((type, index) => ({ id: `node-${index}`, type, data: { type } }));
  const html = renderToStaticMarkup(React.createElement(exports.default, {
    nodes,
    nodeId: nodes.find(node => node.type === 'approval')?.id ?? 'approval',
    edges: definition.edges ?? [],
    data: { type: 'approval', ...data },
    onUpdate: change => updates.push(change),
  }));
  return { html, updates };
}

test('customer input retains the existing internal review action', () => {
  const { html } = render(['input', 'approval', 'answer']);
  assert.match(html, /<option value="create_aftersales_review">创建内部售后核对待办<\/option>/);
  assert.match(html, /value="staff-a"/);
});

for (const source of ['timer', 'event', 'webhook']) {
  test(`${source} never offers a customer review action even with an input node`, () => {
    const { html } = render([source, 'input', 'approval', 'answer']);
    assert.doesNotMatch(html, /<option value="create_aftersales_review"/);
    assert.match(html, /仅继续流程/);
    assert.doesNotMatch(html, /id="review-category"/);
    assert.match(html, source === 'timer' ? /定时任务没有客户会话/ : /事件与外部通知可由员工确认后继续流程/);
    assert.doesNotMatch(html, /暂不支持人工确认/);
  });
}

test('a draft without a source cannot configure customer-specific actions', () => {
  const { html } = render(['approval', 'answer']);
  assert.doesNotMatch(html, /<option value="create_aftersales_review"/);
  assert.match(html, /先添加客户输入节点/);
});

test('a retained incompatible action stays selected and is clearly disabled', () => {
  const data = { after_approval_action: 'create_aftersales_review', case_category: 'quality_issue', summary: '保留原说明' };
  const { html, updates } = render(['timer', 'approval'], data);
  assert.match(html, /<option value="create_aftersales_review" disabled="" selected="">/);
  assert.match(html, /当前触发来源不能使用此动作，请改为“仅继续流程”/);
  assert.match(html, /保留原说明/);
  assert.doesNotMatch(html, /id="review-category"/);
  assert.deepEqual(updates, []);
  assert.equal(data.case_category, 'quality_issue');
});

test('changing the trigger updates available actions without rewriting the saved draft', () => {
  const data = { after_approval_action: 'create_aftersales_review', case_category: 'exchange_request' };
  const first = render(['input', 'approval'], data);
  const timer = render(['timer', 'approval'], data);
  const restored = render(['input', 'approval'], data);
  assert.match(first.html, /id="review-category"/);
  assert.doesNotMatch(timer.html, /id="review-category"/);
  assert.match(restored.html, /value="exchange_request" selected/);
  assert.deepEqual([...first.updates, ...timer.updates, ...restored.updates], []);
});

function variableDefinition() {
  return {
    nodes: [
      { id: 'input', type: 'input', data: { type: 'input', label: '客户问题', reference_key: 'input_1', input_variables: [{ name: 'query', type: 'string' }] } },
      { id: 'approval', type: 'approval', data: { type: 'approval', label: '确认' } },
      { id: 'later', type: 'input', data: { type: 'input', label: '其他输入', reference_key: 'later', input_variables: [{ name: 'private', type: 'string' }] } },
    ],
    edges: [{ source: 'input', target: 'approval' }, { source: 'approval', target: 'later' }],
  };
}

test('approval instructions and choices use the actual connected input reference', () => {
  const { html } = render([], {}, variableDefinition());
  assert.match(html, /<option value="input_1.query">/);
  assert.match(html, /<code>\{\{input_1.query\}\}<\/code>/);
  assert.doesNotMatch(html, /input.order_number|later.private/);
});

test('a disconnected input is not offered as a preview variable', () => {
  const definition = variableDefinition();
  definition.edges = [];
  const { html } = render([], {}, definition);
  assert.match(html, /id="approval-variable"[^>]*disabled=""/);
  assert.doesNotMatch(html, /<option value="input_1.query">|input.order_number/);
});

test('opaque API bodies and headers are excluded while scalar status remains available', () => {
  const definition = variableDefinition();
  definition.nodes[0] = { id: 'input', type: 'api', data: { type: 'api', label: '查询', reference_key: 'api_1' } };
  const { html } = render([], {}, definition);
  assert.match(html, /<option value="api_1.status_code">/);
  assert.doesNotMatch(html, /<option value="api_1.(?:body|headers)">/);
});
