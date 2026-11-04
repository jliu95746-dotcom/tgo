import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function load(file, scope) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(file, import.meta.url), 'utf8')
    .replaceAll('import.meta.env', 'testEnv'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, { exports, console, ...scope });
  return exports;
}
const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

test('all trial transports preserve scope paths, original payload and cancellation signal', async () => {
  const calls = [];
  const api = load('../src/services/api.ts', { localStorage: storage, sessionStorage: storage, testEnv: {},
    require: () => ({ language: 'zh' }), fetch: async (url, options) => {
      calls.push({ url, ...options });
      return { ok: true, status: 200, json: async () => ({ synthetic: true }) };
    },
  });
  const { AgentTrialsApi } = load('../src/services/agentTrialsApi.ts', { require: name => {
    assert.equal(name, './api'); return { ...api, __esModule: true };
  } });
  const signal = new AbortController().signal;
  const request = { request_key: 'request-a', message: 'synthetic question', channel: 'web', timeout: 45 };
  await AgentTrialsApi.start('agent/a', request, signal);
  await AgentTrialsApi.lookup('agent/a', request, signal);
  await AgentTrialsApi.byRequest('agent/a', 'request/a', signal);
  await AgentTrialsApi.get('agent/a', 'report/a', signal);
  await AgentTrialsApi.list('agent/a', signal);
  await AgentTrialsApi.cancel('agent/a', 'report/a', signal);
  assert.deepEqual(calls.map(value => value.url), [
    '/api/v1/ai/agents/agent%2Fa/trials', '/api/v1/ai/agents/agent%2Fa/trials/lookup',
    '/api/v1/ai/agents/agent%2Fa/trials/requests/request%2Fa',
    '/api/v1/ai/agents/agent%2Fa/trials/report%2Fa',
    '/api/v1/ai/agents/agent%2Fa/trials?limit=20',
    '/api/v1/ai/agents/agent%2Fa/trials/report%2Fa/cancel',
  ]);
  assert.ok(calls.every(value => value.signal === signal));
  assert.deepEqual(JSON.parse(calls[0].body), request);
  assert.deepEqual(JSON.parse(calls[1].body), request);
  assert.equal(calls[5].method, 'POST');
  assert.equal(calls[5].body, undefined);
});

test('aborting a trial POST reaches the fetch operation and rejects the pending request', async () => {
  let reached;
  const fetchStarted = new Promise(resolve => { reached = resolve; });
  const api = load('../src/services/api.ts', { localStorage: storage, sessionStorage: storage, testEnv: {},
    require: () => ({ language: 'zh' }), fetch: (_url, options) => {
      reached(options.signal);
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      });
    },
  });
  const controller = new AbortController();
  const sending = api.apiClient.post('/v1/ai/agents/agent-a/trials', { message: 'synthetic' }, { signal: controller.signal });
  assert.equal(await fetchStarted, controller.signal);
  controller.abort();
  await assert.rejects(sending);
});
