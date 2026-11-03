import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/deviceScriptPolicy.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Number, RegExp });
const { prepareScriptPolicy } = exports;
const own = { device_id: 'device', project_id: 'project' };
const item = { script_id: 'daily_report', grant_digest: 'a'.repeat(64), effect: 'read_only',
  timeout_seconds: 20, max_output_bytes: 8192, max_runs: 2 };
const input = () => ({ deviceId: 'device', projectId: 'project',
  local: { ...own, connection_instance_id: 'c'.repeat(32),
    session: { protocol_version: 1, state: 'awaiting_policy', session_id: 'd'.repeat(32) } },
  catalog: { protocol_version: 1, scripts: [item] }, policy: null, file: null, screen: null,
  scriptId: item.script_id, timeoutSeconds: 10, maxOutputBytes: 4096, maxRuns: 1 });

test('publication carries only current reviewed identity and bounded limits', () => {
  const result = prepareScriptPolicy(input());
  assert.equal(result.expected_policy_revision, 0);
  assert.equal(result.session_id, 'd'.repeat(32));
  assert.equal(result.connection_instance_id, 'c'.repeat(32));
  assert.equal(result.grant_digest, item.grant_digest);
  assert.equal(result.max_runs, 1);
  assert.equal('command' in result, false);
  assert.equal('parameters' in result, false);
});

test('missing scopes, write scripts, active other modes and invalid numbers cannot publish', () => {
  for (const patch of [
    { local: null }, { deviceId: 'other' }, { projectId: 'other' },
    { scriptId: 'unreviewed' }, { maxRuns: 3 }, { maxRuns: 1.5 },
    { timeoutSeconds: NaN }, { timeoutSeconds: 21 }, { maxOutputBytes: 8193 },
    { maxOutputBytes: 255 }, { catalog: { protocol_version: 1, scripts: [{ ...item, effect: 'write' }] } },
    { local: { ...input().local, session: { ...input().local.session, state: 'closed' } } },
    { local: { ...input().local, connection_instance_id: 'stale' } },
    { file: { ...own, status: 'pending' } }, { screen: { ...own, status: 'active' } },
    { file: { ...own, project_id: 'foreign', status: 'revoked' } },
    { policy: { ...own, device_id: 'other', status: 'revoked', policy: { revision: 1 } } },
  ]) assert.equal(prepareScriptPolicy({ ...input(), ...patch }), null);
});

test('retired history preserves the revision while allowing an explicit new publication', () => {
  const result = prepareScriptPolicy({ ...input(), file: { ...own, status: 'revoked' },
    policy: { ...own, status: 'revoked', policy: { revision: 7 } } });
  assert.equal(result.expected_policy_revision, 7);
});

test('a generic command listing cannot prove reviewed report execution', () => {
  const setup = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/deviceSetup.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: setup });
  assert.equal(setup.supportsDeviceScenario('report', [{ name: 'shell_exec' }]), false);
  assert.equal(setup.supportsDeviceScenario('report', [{ name: 'scripts/run' }]), false);
});
