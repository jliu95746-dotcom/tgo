import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture(base = '/api') {
  const exports = {}, calls = [];
  const api = Object.fromEntries(['get', 'put', 'post'].map(method => [method, async (...args) => { calls.push([method, ...args]); return {}; }]));
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/workflowWebhookApi.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, { exports, URL, window: { location: { origin: 'https://console.example.invalid' } }, require: name => {
    if (name === './api') return { __esModule: true, default: api, getApiBaseUrl: () => base };
    throw Error(name);
  } });
  return { ...exports, calls };
}
test('delivery address follows the configured API root rather than a fixed domain', () => {
  assert.equal(fixture().webhookIngressUrl('hook-a'), 'https://console.example.invalid/api/v1/workflow-webhooks/hook-a');
  assert.equal(fixture('https://api.example.invalid/custom/').webhookIngressUrl('hook-a'), 'https://api.example.invalid/custom/v1/workflow-webhooks/hook-a');
});
test('management requests encode identifiers and preserve original command and abort signal', async () => {
  const h = fixture(), signal = new AbortController().signal, request = { request_id: 'command-a', expected_revision: 4 };
  await h.workflowWebhookApi.rotate('flow/a', request, signal);
  assert.equal(h.calls[0][0], 'post'); assert.equal(h.calls[0][1], '/v1/ai/workflows/flow%2Fa/webhook/rotate');
  assert.equal(h.calls[0][2], request); assert.equal(h.calls[0][3].signal, signal);
  await h.workflowWebhookApi.getCommand('flow/a', 'command/b', signal);
  assert.equal(h.calls[1][1], '/v1/ai/workflows/flow%2Fa/webhook/commands/command%2Fb');
});
test('history paging remains a read request and never contains a credential', async () => {
  const h = fixture(); await h.workflowWebhookApi.getHistory('flow-a', 20);
  assert.equal(h.calls[0][0], 'get'); assert.equal(h.calls[0][1], '/v1/ai/workflows/flow-a/webhook/deliveries?limit=10&offset=20');
  assert.doesNotMatch(JSON.stringify(h.calls), /credential|secret/);
});
