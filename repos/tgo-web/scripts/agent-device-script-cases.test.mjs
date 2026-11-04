import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function load(path, overrides = {}) {
  const exports = {};
  const source = readFileSync(new URL('../src/' + path, import.meta.url), 'utf8').replaceAll('import.meta.env', 'testEnv');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  } }).outputText, { exports, Date, TextEncoder, console, testEnv: {},
    require: name => ['@/utils/agentDeviceScriptValidation', './agentDeviceScriptValidation'].includes(name) ? load('utils/agentDeviceScriptValidation.ts') : { language: 'zh' }, ...overrides });
  return exports;
}
const project = randomUUID(), actor = randomUUID(), employee = randomUUID();
const item = () => ({ scope: 'employee_device_script_case', can_enable: false,
  id: randomUUID(), case_id: randomUUID(), project_id: project, agent_id: employee, created_by: actor,
  request_id: randomUUID(), revision: 1, title: '日报测试', binding_id: randomUUID(), binding_revision: 2,
  device_id: randomUUID(), policy_digest: 'a'.repeat(64), grant_digest: 'b'.repeat(64), case_digest: 'c'.repeat(64),
  status: 'current', created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString() });

test('live run checks reject stale counts, foreign reports and missing or revoked approval', () => {
  const { validScriptRunCheck } = load('utils/agentDeviceScriptCaseValidation.ts');
  const report = randomUUID(), now = Date.now();
  const review = { scope: 'employee_device_script_review', can_enable: false,
    project_id: project, actor_id: actor, agent_id: employee, report_id: report,
    status: 'approved', review: { id: randomUUID(), case_version_id: randomUUID(),
      case_digest: 'a'.repeat(64), result_digest: 'b'.repeat(64), decision: 'approved',
      reviewed_at: new Date(now - 60000).toISOString(),
      expires_at: new Date(now + 60000).toISOString(), revoked_at: null } };
  const check = { scope: 'employee_device_script_run_check', can_enable: false,
    review, status: 'ready', remaining_runs: 2,
    checked_at: new Date(now).toISOString(), valid_until: new Date(now + 29000).toISOString() };
  const validate = value => validScriptRunCheck(value, project, actor, employee, report);
  assert.ok(validate(check));
  for (const change of [
    { checked_at: new Date(now - 31000).toISOString() },
    { valid_until: new Date(now + 31000).toISOString() },
    { remaining_runs: 0 }, { remaining_runs: true }, { can_enable: true },
    { review: { ...review, actor_id: randomUUID() } },
    { review: { ...review, report_id: randomUUID() } },
    { review: { ...review, review: null } },
    { review: { ...review, review: { ...review.review, revoked_at: new Date(now).toISOString() } } },
  ]) assert.equal(validate({ ...check, ...change }), false);
  assert.ok(validate({ ...check, status: 'budget_exhausted', remaining_runs: 0, valid_until: null }));
  assert.equal(validate({ ...check, status: 'budget_exhausted', remaining_runs: 1, valid_until: null }), false);
});

test('case and evidence reads bypass cache and preserve the original identifier and signal', async () => {
  const calls = [], storage = { getItem: () => null };
  const api = load('services/api.ts', { localStorage: storage, sessionStorage: storage,
    fetch: async (url, options) => { calls.push({ url, ...options }); return { ok: true, status: 200, json: async () => ({}) }; } });
  const { AgentDeviceScriptCasesApi: service } = load('services/agentDeviceScriptCasesApi.ts', {
    require: () => ({ ...api, __esModule: true }),
  });
  const signal = new AbortController().signal;
  await service.list('agent/a', 20, signal); await service.read('agent/a', 'version/a', signal);
  await service.recover('agent/a', 'request/a', signal); await service.evidence('agent/a', 'report/a', signal);
  assert.ok(calls.every(call => call.cache === 'no-store' && call.signal === signal && call.method === 'GET' && call.body === undefined));
  assert.equal(calls[0].url, '/api/v1/ai/agents/agent%2Fa/device-script-cases?skip=20&limit=20');
  assert.ok(calls[1].url.endsWith('/versions/version%2Fa'));
  assert.ok(calls[2].url.endsWith('/requests/request%2Fa'));
  assert.ok(calls[3].url.endsWith('/report%2Fa/evidence'));
  const control = { request_id: randomUUID(), binding_id: randomUUID(), binding_revision: 1 };
  await service.run('agent/a', 'version/a', control, signal);
  assert.deepEqual(JSON.parse(calls[4].body), control);
  assert.ok(calls[4].url.endsWith('/versions/version%2Fa/trials'));
  await service.saveState('agent/a', 'request/a', signal);
  assert.ok(calls[5].url.endsWith('/requests/request%2Fa/state'));
  assert.equal(calls[5].cache, 'no-store');
  const stop = { ...control, case_id: null, expected_revision: 0 };
  await service.cancelSave('agent/a', stop, signal);
  assert.deepEqual(JSON.parse(calls[6].body), stop);
  assert.ok(calls[6].url.endsWith(`/requests/${stop.request_id}/cancel`));
  await service.checkRun('agent/a', 'report/a', signal);
  assert.ok(calls[7].url.endsWith('/report%2Fa/review/run-check'));
  assert.equal(calls[7].method, 'GET');
  assert.equal(calls[7].cache, 'no-store');
  assert.equal(calls[7].body, undefined);
  assert.equal(calls[7].signal, signal);
});

test('case metadata rejects another owner, hidden bodies and forged approval', () => {
  const { validScriptCase } = load('utils/agentDeviceScriptCaseValidation.ts');
  const original = item();
  assert.ok(validScriptCase(original, project, employee));
  for (const change of [{ project_id: randomUUID() }, { agent_id: randomUUID() }, { can_enable: true },
    { parameters: { private: true } }, { revision: true }, { case_digest: 'invalid' }]) {
    assert.equal(validScriptCase({ ...original, ...change }, project, employee), false);
  }
});

test('unavailable evidence cannot expose private bodies; available evidence must belong to the selected report', () => {
  const { validScriptEvidence } = load('utils/agentDeviceScriptCaseValidation.ts');
  const report = randomUUID(), saved = item();
  const value = { scope: 'employee_device_script_evidence', can_enable: false, project_id: project,
    actor_id: actor, agent_id: employee, report_id: report, status: 'available', case: saved,
    values: { parameters: { label: 'private' }, expected_fields: { ready: true } }, result_fields: { ready: true },
    captured_at: new Date().toISOString(), expires_at: saved.expires_at };
  assert.ok(validScriptEvidence(value, project, actor, employee, report));
  for (const change of [{ status: 'expired' }, { report_id: randomUUID() }, { actor_id: randomUUID() },
    { case: { ...saved, agent_id: randomUUID() } }, { values: null }, { result_fields: { ready: {} } }]) {
    assert.equal(validScriptEvidence({ ...value, ...change }, project, actor, employee, report), false);
  }
  assert.ok(validScriptEvidence({ ...value, status: 'not_retained', case: null, values: null,
    result_fields: null, captured_at: null, expires_at: null }, project, actor, employee, report));
});
