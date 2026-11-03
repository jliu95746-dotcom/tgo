import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function load(file, scope) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(file, import.meta.url), 'utf8').replaceAll('import.meta.env', 'testEnv'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, { exports, console, ...scope });
  return exports;
}
const storage = { getItem: () => null, setItem() {}, removeItem() {} };
function clients(fetch) {
  const api = load('../src/services/api.ts', { localStorage: storage, sessionStorage: storage, testEnv: {}, require: () => ({ language: 'zh' }), fetch });
  const dependency = name => { assert.equal(name, './api'); return { ...api, __esModule: true }; };
  return { ...load('../src/services/agentActivationPoliciesApi.ts', { require: dependency }),
    ...load('../src/services/businessScenariosApi.ts', { require: dependency }) };
}
test('policy and readiness transport preserves revisions, paths, selection and abort signals', async () => {
  const calls = [], signal = new AbortController().signal;
  const { AgentActivationPoliciesApi: api, BusinessScenariosApi: business } = clients(async (url, options) => {
    calls.push({ url, ...options }); return { ok: true, status: 200, json: async () => ({ fixture: true }) };
  });
  const command = { expected_revision: 6, selection: { template_id: 'knowledge', template_version: 8, channel: 'web' } };
  await api.read('agent/a', signal); await api.readiness('agent/a', signal);
  await api.save('agent/a', command, signal); await api.remove('agent/a', 'web', 6, signal);
  await business.templates('agents', signal);
  assert.deepEqual(calls.map(row => row.url), ['/api/v1/ai/agents/agent%2Fa/activation-policies',
    '/api/v1/ai/agents/agent%2Fa/activation-readiness', '/api/v1/ai/agents/agent%2Fa/activation-policies/web',
    '/api/v1/ai/agents/agent%2Fa/activation-policies/web?expected_revision=6', '/api/v1/business-scenarios/templates?module=agents']);
  assert.ok(calls.every(row => row.signal === signal));
  assert.deepEqual(JSON.parse(calls[2].body), command);
  assert.equal(calls[2].method, 'PUT'); assert.equal(calls[3].method, 'DELETE');
});
for (const method of ['save', 'remove']) test(`canceling a policy ${method} reaches the pending network request`, async () => {
  const controller = new AbortController();
  let reached;
  const started = new Promise(resolve => { reached = resolve; });
  const { AgentActivationPoliciesApi: api } = clients((_url, options) => {
    reached(options.signal);
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
  });
  const request = method === 'save' ? api.save('agent-a', { expected_revision: 0, selection: { template_id: 'knowledge', template_version: 1, channel: 'web' } }, controller.signal)
    : api.remove('agent-a', 'web', 1, controller.signal);
  assert.equal(await started, controller.signal);
  controller.abort(); await assert.rejects(request);
});
