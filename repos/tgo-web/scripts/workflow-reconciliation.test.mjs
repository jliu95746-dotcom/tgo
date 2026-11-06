import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(path, modules) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(code, {
    exports, URLSearchParams, JSON,
    require: name => name in modules ? modules[name] : require(name),
  });
  return exports;
}

const helpers = load('../src/utils/workflowReconciliation.ts', {});
const review = { execution_id: 'run', workflow_id: 'flow', nodes: [
  { id: 'receipt', node_id: 'answer', status: 'completed' },
] };
const execution = { id: 'run', workflow_id: 'flow', node_executions: [
  { id: 'receipt', node_id: 'answer', status: 'completed' },
] };

test('saved answer text keeps line breaks, markdown, zero and false', () => {
  assert.equal(helpers.reconciliationOutput({ result: '**已确认**\n下一行' }), '**已确认**\n下一行');
  assert.equal(helpers.reconciliationOutput(0), '0');
  assert.equal(helpers.reconciliationOutput(false), 'false');
  assert.equal(helpers.reconciliationOutput(null), '');
});

test('continuation preview requires the exact execution and step receipts', () => {
  assert.equal(helpers.reconciliationDetailsMatch(review, execution), true);
  for (const changed of [null, { ...execution, id: 'other' },
    { ...execution, workflow_id: 'other' }, { ...execution, node_executions: [] },
    { ...execution, node_executions: null }, { ...execution, node_executions: [
      { id: 'receipt', node_id: 'answer', status: 'running' },
    ] }]) {
    assert.equal(helpers.reconciliationDetailsMatch(review, changed), false);
  }
  assert.equal(helpers.reconciliationDetailsMatch({ ...review, nodes: [] }, execution), false);
});

test('the API keeps identity server-side, encodes identifiers and binds the preview', async () => {
  const calls = [];
  const api = {
    get: async (...args) => { calls.push(['GET', ...args]); return []; },
    post: async (...args) => { calls.push(['POST', ...args]); return {}; },
  };
  const { WorkflowApiService } = load('../src/services/workflowApi.ts', {
    './api': api, '@/i18n': { t: key => key },
  });
  await WorkflowApiService.listReconciliations({ id: 'approval', requested_at: '2026-11-06T00:00:00Z' });
  const query = new URL('http://fixture' + calls[0][1]);
  assert.deepEqual([...query.searchParams.keys()], ['limit', 'before_requested_at', 'before_id']);
  assert.equal(query.searchParams.get('before_id'), 'approval');
  await WorkflowApiService.getReconciliation('a/b');
  assert.equal(calls[1][1], '/v1/ai/workflows/reconciliations/a%2Fb');
  await WorkflowApiService.decideReconciliation('approval', 'continue', 'a'.repeat(64));
  assert.equal(calls[2][1], '/v1/ai/workflows/reconciliations/approval/decision');
  assert.equal(JSON.stringify(calls[2][2]), JSON.stringify({ decision: 'continue', review_digest: 'a'.repeat(64) }));
});
