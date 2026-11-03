import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const project = randomUUID(), actor = randomUUID(), employee = randomUUID(), device = randomUUID();
const binding = { scope: 'employee_device_script_configuration', can_enable: false,
  id: randomUUID(), project_id: project, agent_id: employee, device_id: device,
  script_id: 'daily_report', policy_digest: 'a'.repeat(64), grant_digest: 'b'.repeat(64),
  revision: 2, enabled: true, configured_by: actor };
const descriptor = { script_id: 'daily_report', grant_digest: 'b'.repeat(64), title: '日报',
  effect: 'read_only', version: 'v1', parameters: [], result_fields: [],
  timeout_seconds: 10, max_output_bytes: 4096, max_runs: 2 };
const policy = { device_id: device, project_id: project, status: 'active', verified: false,
  policy: { ...descriptor, digest: 'a'.repeat(64), revision: 1 } };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const report = command => ({ scope: 'employee_device_script_trial', can_enable: false, assessment: 'not_reviewed',
  id: randomUUID(), project_id: project, actor_id: actor, agent_id: employee,
  request_id: command.request_id, device_id: device, execution_id: randomUUID(),
  binding_id: command.binding_id, binding_revision: command.binding_revision,
  policy_digest: binding.policy_digest, grant_digest: binding.grant_digest,
  expected_digest: 'c'.repeat(64), result_digest: 'c'.repeat(64), status: 'matched',
  error_code: null, remaining_runs: 0, applicability: 'current',
  created_at: new Date().toISOString(), completed_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 3600000).toISOString(), dispatch_claimed_at: new Date().toISOString() });
const requestState = (command, extra = {}) => ({
  scope: 'employee_device_script_trial_request', can_enable: false, project_id: project, actor_id: actor, agent_id: employee,
  request_id: command.request_id, binding_id: command.binding_id, binding_revision: command.binding_revision,
  status: 'ready', stop_status: 'finished', created_at: new Date().toISOString(), deadline_at: new Date(Date.now() + 30000).toISOString(),
  completed_at: new Date().toISOString(), error_code: null, report: report(command), ...extra,
});
const savedCase = (changes = {}) => ({ scope: 'employee_device_script_case', can_enable: false,
  id: randomUUID(), case_id: randomUUID(), project_id: project, agent_id: employee, created_by: actor,
  request_id: randomUUID(), revision: 1, title: '日报核验', binding_id: binding.id, binding_revision: binding.revision,
  device_id: device, policy_digest: binding.policy_digest, grant_digest: binding.grant_digest, case_digest: 'c'.repeat(64),
  status: 'current', created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString(), ...changes });
const saveState = (command, changes = {}) => ({ scope: 'employee_device_script_case_save', can_enable: false,
  project_id: project, actor_id: actor, agent_id: employee, request_id: command.request_id,
  binding_id: command.binding_id, binding_revision: command.binding_revision, case_id: command.case_id ?? null,
  expected_revision: command.expected_revision ?? 0, status: 'cancelled', case: null, error_code: 'cancelled',
  created_at: new Date().toISOString(), deadline_at: new Date().toISOString(), completed_at: new Date().toISOString(), ...changes });

function harness(overrides = {}, storage = new Map(), writable = true) {
  let user = { id: actor, project_id: project, role: 'admin' };
  const calls = [], listeners = [];
  let lastReport = null;
  class APIError extends Error { constructor(status) { super('fixture'); this.status = status; } }
  const api = {
    readBinding: async () => ({ project_id: project, agent_id: employee, binding }),
    saveBinding: async (_agent, command) => ({ ...binding, revision: command.expected_revision + 1, enabled: command.enabled }),
    recoverBinding: async () => binding,
    start: async (_agent, command) => report(command),
    recover: async () => { throw new APIError(404); },
    requestState: async () => {
      if (!lastReport) throw new APIError(404);
      return requestState(lastReport, { report: lastReport });
    },
    cancelRequest: async () => { throw new APIError(404); },
    runCase: async (_agent, version, command) => ({ scope: 'employee_device_script_case_trial', can_enable: false,
      case_version_id: version, report: report(command) }),
    listCases: async (_agent, skip) => ({ project_id: project, agent_id: employee, items: [], total: 0, skip, limit: 20 }),
    saveCase: async (_agent, command) => savedCase({ request_id: command.request_id, case_id: command.case_id ?? randomUUID(),
      revision: command.expected_revision + 1, title: command.title }),
    recoverCase: async () => { throw new APIError(404); },
    caseSaveState: async () => { throw new APIError(404); },
    cancelCaseSave: async (_agent, command) => saveState(command),
    ...overrides,
  };
  const wrapped = Object.fromEntries(Object.entries(api).map(([name, fn]) => [name, (...args) => {
    calls.push({ name, args }); const result = fn(...args);
    return ['start', 'runCase'].includes(name) ? Promise.resolve(result).then(value => { lastReport = name === 'start' ? value : value.report; return value; }) : result;
  }]));
  const modules = {};
  function load(path) {
    if (modules[path]) return modules[path];
    const exports = {};
    const source = ts.transpileModule(readFileSync(new URL('../src/' + path, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(source, { exports, Date, Number, Set, JSON, RegExp, TextEncoder, AbortController,
      crypto: { randomUUID }, sessionStorage: {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => { if (!writable) throw Error('blocked storage'); storage.set(key, value); },
        removeItem: key => storage.delete(key),
      }, require: name => {
        if (name === './authStore') return { useAuthStore: { getState: () => ({ user }), subscribe: fn => listeners.push(fn) } };
        if (name === '@/services/api') return { APIError };
        if (name === '@/services/agentDeviceScriptsApi') return { AgentDeviceScriptsApi: wrapped };
        if (name === '@/services/agentDeviceScriptCasesApi') return { AgentDeviceScriptCasesApi: {
          run: wrapped.runCase, list: wrapped.listCases, save: wrapped.saveCase, recover: wrapped.recoverCase,
          saveState: wrapped.caseSaveState, cancelSave: wrapped.cancelCaseSave,
        } };
        if (name === './agentDeviceScriptStore') return load('stores/agentDeviceScriptStore.ts');
        if (name === './agentBusinessCurrentStateStore') return { useAgentBusinessCurrentStateStore: { getState: () => ({ check: async () => overrides.paused !== false }) } };
        if (name === './agentActivationStore') return { useAgentActivationStore: { getState: () => ({ invalidate: () => {} }) } };
        if (name === '@/services/deviceControlApi') return {
          listDevices: async () => ({ devices: [{ id: device, project_id: project, device_name: '本机', status: 'online' }], total: 1 }),
          getDeviceScriptPolicy: async () => policy,
          getDeviceScriptCatalog: async () => ({ protocol_version: 1, scripts: [descriptor] }),
          getDeviceScriptStatus: async () => ({ device_id: device, project_id: project, connection_instance_id: 'e'.repeat(32),
            session: { protocol_version: 1, state: 'active', session_id: 'f'.repeat(32) } }),
        };
        if (name === '@/utils/agentDeviceScriptValidation') return load('utils/agentDeviceScriptValidation.ts');
        if (name === './agentDeviceScriptValidation') return load('utils/agentDeviceScriptValidation.ts');
        if (name === '@/utils/agentDeviceScriptCaseValidation') return load('utils/agentDeviceScriptCaseValidation.ts');
        return require(name);
      } });
    modules[path] = exports; return exports;
  }
  return { store: load('stores/agentDeviceScriptStore.ts').useAgentDeviceScriptStore,
    cases: load('stores/agentDeviceScriptCasesStore.ts').useAgentDeviceScriptCasesStore, calls, storage,
    change(next) { const previous = { user }; user = next; listeners.forEach(fn => fn({ user }, previous)); } };
}
async function opened(h) { await h.store.getState().select(employee); await h.store.getState().chooseDevice(device); }

test('the original request pointer is durable before POST; private values never enter shared state or storage', async () => {
  const pending = deferred(), h = harness({ start: () => pending.promise });
  await opened(h);
  const operation = h.store.getState().start({ label: 'PRIVATE-INPUT' }, { ready: true }); await tick();
  const command = h.calls.find(call => call.name === 'start').args[1];
  assert.ok([...h.storage.values()].some(value => value.includes(command.request_id)));
  assert.equal(JSON.stringify(h.store.getState()).includes('PRIVATE-INPUT'), false);
  assert.equal([...h.storage.values()].join('').includes('PRIVATE-INPUT'), false);
  pending.resolve(report(command)); await operation;
  assert.equal(h.store.getState().report.status, 'matched');
  assert.equal(h.store.getState().report.can_enable, false);
});

test('a saved version uses the original trial recovery and sends only metadata', async () => {
  const h = harness(), saved = savedCase(); await opened(h);
  await h.store.getState().startCase(saved);
  const calls = h.calls.filter(call => call.name === 'runCase');
  assert.equal(calls.length, 1); assert.equal(calls[0].args[1], saved.id);
  assert.deepEqual(Object.keys(calls[0].args[2]).sort(), ['binding_id', 'binding_revision', 'request_id']);
  assert.equal(h.store.getState().report.status, 'matched');
  assert.equal(h.calls.filter(call => call.name === 'start').length, 0);
  assert.deepEqual(Object.keys(JSON.parse([...h.storage.values()][0])).sort(), ['bindingPending', 'trialPointer']);
});

test('unknown saved-case dispatch cannot be retried and a different version response is rejected', async () => {
  for (const runCase of [async () => { throw Error('lost'); }, async (_agent, _version, command) => ({
    scope: 'employee_device_script_case_trial', can_enable: false, case_version_id: randomUUID(), report: report(command),
  })]) {
    const h = harness({ runCase, requestState: async () => { throw Error('unconfirmed'); } }); await opened(h);
    await h.store.getState().startCase(savedCase()); await h.store.getState().recover();
    await h.store.getState().startCase(savedCase());
    assert.equal(h.calls.filter(call => call.name === 'runCase').length, 1);
    assert.equal(h.store.getState().unknown, true); assert.equal(h.store.getState().report, null);
  }
});

test('expired, changed and superseded versions cannot dispatch', async () => {
  for (const change of [{ status: 'superseded' }, { status: 'changed' }, { binding_revision: 1 },
    { expires_at: new Date(0).toISOString() }, { can_enable: true }]) {
    const h = harness(); await opened(h); await h.store.getState().startCase(savedCase(change));
    assert.equal(h.calls.filter(call => call.name === 'runCase').length, 0);
  }
});

test('case saves keep private bodies out of shared state and durable recovery metadata', async () => {
  const h = harness(); await opened(h); await h.cases.getState().select(employee);
  assert.equal(await h.cases.getState().save('日报', { label: 'PRIVATE-INPUT' }, { label: 'PRIVATE-EXPECTED' }), true);
  const command = h.calls.find(call => call.name === 'saveCase').args[1];
  assert.equal(command.parameters.label, 'PRIVATE-INPUT');
  assert.equal(JSON.stringify(h.cases.getState()).includes('PRIVATE-'), false);
  assert.equal([...h.storage.values()].join('').includes('PRIVATE-'), false);
  assert.equal(h.calls.some(call => ['runCase', 'start'].includes(call.name)), false);
});

test('a lost case save is only recovered by its original identifier across reopening', async () => {
  const h = harness({ saveCase: async () => { throw Error('unknown'); } }); await opened(h); await h.cases.getState().select(employee);
  await h.cases.getState().save('日报', { label: 'private' }, { ready: true });
  const command = h.calls.find(call => call.name === 'saveCase').args[1];
  await h.cases.getState().recover(); await h.cases.getState().save('重复', {}, { ready: true });
  assert.equal(h.calls.filter(call => call.name === 'saveCase').length, 1);
  assert.equal(h.cases.getState().pending.requestId, command.request_id);
  const reopened = harness({ caseSaveState: async (_agent, request) => {
    assert.equal(request, command.request_id); return saveState(command, { status: 'saved', error_code: null,
      case: savedCase({ request_id: request }) });
  } }, h.storage);
  await reopened.cases.getState().select(employee);
  assert.equal(reopened.cases.getState().pending, null);
  assert.equal(reopened.calls.filter(call => call.name === 'saveCase').length, 0);
});

test('missing save state is not success; explicit cancel fences it and allows a fresh save', async () => {
  let attempt = 0;
  const h = harness({ saveCase: async (_agent, command) => {
    if (++attempt === 1) throw Error('lost response');
    return savedCase({ request_id: command.request_id });
  } });
  await opened(h); await h.cases.getState().select(employee);
  await h.cases.getState().save('日报', { label: 'PRIVATE-INPUT' }, { ready: true });
  const original = h.cases.getState().pending.requestId;
  await h.cases.getState().recover();
  assert.equal(h.cases.getState().pending.requestId, original);
  await h.cases.getState().cancel();
  assert.equal(h.cases.getState().pending, null);
  assert.equal(h.cases.getState().saveState.status, 'cancelled');
  const control = h.calls.find(call => call.name === 'cancelCaseSave').args[1];
  assert.equal(Object.keys(control).sort().join(), 'binding_id,binding_revision,case_id,expected_revision,request_id');
  assert.equal(control.request_id, original);
  assert.equal(await h.cases.getState().save('新用例', {}, { ready: true }), true);
  assert.equal(h.calls.filter(call => call.name === 'saveCase').length, 2);
  assert.equal(JSON.stringify(h.cases.getState()).includes('PRIVATE-'), false);
});

test('cancel is possible during save and its terminal state ignores late POST and GET results', async () => {
  for (const operation of ['post', 'read']) {
    const delayed = deferred();
    const h = harness({ saveCase: async () => operation === 'post' ? delayed.promise : Promise.reject(Error('lost')),
      caseSaveState: async () => delayed.promise });
    await opened(h); await h.cases.getState().select(employee);
    const posting = h.cases.getState().save('日报', {}, { ready: true }); await tick();
    const checking = operation === 'read' ? h.cases.getState().recover() : Promise.resolve(); await tick();
    await h.cases.getState().cancel();
    assert.equal(h.cases.getState().pending, null);
    const original = h.calls.find(call => call.name === 'saveCase').args[1];
    delayed.resolve(operation === 'post' ? savedCase({ request_id: original.request_id })
      : saveState(original, { status: 'preparing', completed_at: null, error_code: null }));
    await posting; await checking;
    assert.equal(h.cases.getState().saveState.status, 'cancelled');
    assert.equal(h.cases.getState().lastSaved, null);
    assert.equal(h.cases.getState().busy, false);
    assert.equal(h.calls.filter(call => call.name === 'saveCase').length, 1);
  }
});

test('failed save recovery unlocks only after an exact terminal receipt', async () => {
  let response;
  const h = harness({ saveCase: async () => { throw Error('lost'); }, caseSaveState: async () => response });
  await opened(h); await h.cases.getState().select(employee);
  await h.cases.getState().save('日报', {}, { ready: true });
  const original = h.calls.find(call => call.name === 'saveCase').args[1];
  for (const fault of [{ actor_id: randomUUID() }, { can_enable: true }, { completed_at: null }, { parameters: { private: true } }]) {
    response = saveState(original, { status: 'failed', error_code: 'preparation_expired', ...fault });
    await h.cases.getState().recover();
    assert.equal(h.cases.getState().pending.requestId, original.request_id);
  }
  response = saveState(original, { status: 'failed', error_code: 'preparation_expired' });
  await h.cases.getState().recover();
  assert.equal(h.cases.getState().pending, null);
  assert.equal(h.cases.getState().saveState.status, 'failed');
});

test('case saves recheck paused state and reject storage failures and late account responses', async () => {
  for (const [options, writable] of [[{ paused: false }, true], [{}, false]]) {
    const h = harness(options, new Map(), writable); await opened(h); await h.cases.getState().select(employee);
    await h.cases.getState().save('日报', {}, { ready: true });
    assert.equal(h.calls.filter(call => call.name === 'saveCase').length, 0);
  }
  const pending = deferred(), h = harness({ saveCase: () => pending.promise }); await opened(h); await h.cases.getState().select(employee);
  const saving = h.cases.getState().save('日报', {}, { ready: true }); await tick();
  const command = h.calls.find(call => call.name === 'saveCase').args[1];
  h.change({ id: randomUUID(), project_id: project, role: 'admin' });
  pending.resolve(savedCase({ request_id: command.request_id })); await saving;
  assert.equal(h.cases.getState().lastSaved, null); assert.equal(h.cases.getState().agentId, null);
});

test('unknown responses, including a recovery 404, block another execution and survive reopening', async () => {
  const h = harness({ start: async () => { throw Error('lost response'); } }); await opened(h);
  await h.store.getState().start({}, { ready: true });
  const command = h.calls.find(call => call.name === 'start').args[1];
  await h.store.getState().start({}, { ready: true }); await h.store.getState().recover();
  assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
  assert.equal(h.store.getState().unknown, true);
  const reopened = harness({ requestState: async (_agent, key) => { assert.equal(key, command.request_id); return requestState(command); } }, h.storage);
  await reopened.store.getState().select(employee);
  assert.equal(reopened.store.getState().report.request_id, command.request_id);
  assert.equal(reopened.calls.filter(call => call.name === 'start').length, 0);
});

test('double clicks make one POST; changing accounts ignores a late result', async () => {
  const pending = deferred(), h = harness({ start: () => pending.promise }); await opened(h);
  const operation = h.store.getState().start({}, { ready: true }); await tick();
  await h.store.getState().start({}, { ready: true });
  const command = h.calls.find(call => call.name === 'start').args[1];
  h.change({ id: randomUUID(), project_id: project, role: 'admin' });
  pending.resolve(report(command)); await operation;
  assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
  assert.equal(h.store.getState().report, null);
  assert.equal(h.store.getState().agentId, null);
});

test('a fresh paused check and writable recovery storage are required before dispatch', async () => {
  for (const [overrides, writable] of [[{ paused: false }, true], [{}, false]]) {
    const h = harness(overrides, new Map(), writable); await opened(h);
    await h.store.getState().start({}, { ready: true });
    assert.equal(h.calls.filter(call => call.name === 'start').length, 0);
  }
});

test('a foreign or approval-bearing response is never treated as a successful trial', async () => {
  for (const invalid of [{ actor_id: randomUUID() }, { can_enable: true }, { fields: { PRIVATE: true } }, { result_digest: 'd'.repeat(64) }]) {
    const h = harness({ start: async (_agent, command) => ({ ...report(command), ...invalid }) }); await opened(h);
    await h.store.getState().start({}, { ready: true });
    assert.equal(h.store.getState().report, null); assert.equal(h.store.getState().unknown, true);
  }
});

test('a lost binding save is recovered with GET and never auto-replayed', async () => {
  let recovered = false;
  const h = harness({ saveBinding: async () => { throw Error('lost save'); },
    recoverBinding: async () => { recovered = true; return { ...binding, revision: 3 }; },
    readBinding: async () => ({ project_id: project, agent_id: employee, binding: { ...binding, revision: recovered ? 3 : 2 } }),
  });
  await opened(h); await h.store.getState().saveBinding(true);
  assert.ok(h.store.getState().bindingPending);
  await h.store.getState().saveBinding(true);
  assert.equal(h.calls.filter(call => call.name === 'saveBinding').length, 1);
  await h.store.getState().recover();
  assert.equal(h.store.getState().bindingPending, null);
  assert.equal(h.store.getState().binding.revision, 3);
});

test('corrupt recovery data blocks dispatch rather than silently forgetting a request', async () => {
  const storage = new Map([[`tgo:employee-script:v1:${project}:${actor}:${employee}`, JSON.stringify({ bindingPending: false, trialPointer: null })]]);
  const h = harness({}, storage); await h.store.getState().select(employee);
  await h.store.getState().start({}, { ready: true });
  assert.equal(h.calls.length, 0); assert.equal(h.store.getState().error, 'storage');
});

test('ordinary staff cannot load script configuration or start a trial', async () => {
  const h = harness(); h.change({ id: actor, project_id: project, role: 'user' });
  await h.store.getState().select(employee); await h.store.getState().start({}, { ready: true });
  assert.equal(h.calls.length, 0);
});

test('preparing and failed preparation states can be recovered without re-POSTing', async () => {
  let status = 'preparing';
  const h = harness({ start: async () => { throw Error('lost response'); },
    requestState: async (_agent, key) => {
      const command = h.calls.find(call => call.name === 'start').args[1];
      assert.equal(key, command.request_id);
      return requestState(command, { status, stop_status: 'none', report: null,
        completed_at: status === 'preparing' ? null : new Date().toISOString(), error_code: status === 'preparing' ? null : 'preparation_timeout' });
    },
  });
  await opened(h); await h.store.getState().start({}, { ready: true });
  await h.store.getState().recover();
  assert.equal(h.store.getState().requestState.status, 'preparing'); assert.equal(h.store.getState().unknown, true);
  status = 'failed'; await h.store.getState().recover();
  assert.equal(h.store.getState().unknown, false); assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
});

test('cancel is available while submission is pending and a late failure cannot undo its confirmed fence', async () => {
  const pending = deferred();
  const h = harness({ start: () => pending.promise, cancelRequest: async (_agent, command) => requestState(command, {
    status: 'cancelled', stop_status: 'confirmed', report: null, error_code: 'cancelled',
  }) });
  await opened(h); const submit = h.store.getState().start({}, { ready: true }); await tick();
  await h.store.getState().cancel();
  assert.equal(h.store.getState().requestState.status, 'cancelled'); assert.equal(h.store.getState().unknown, false);
  const body = h.calls.find(call => call.name === 'cancelRequest').args[1];
  assert.equal(Object.keys(body).sort().join(), 'binding_id,binding_revision,request_id');
  pending.reject(Error('cancelled before dispatch')); await submit;
  assert.equal(h.store.getState().requestState.status, 'cancelled'); assert.equal(h.store.getState().unknown, false);
  assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
});

test('a stop request is not a stopped device; original receipt confirmation unlocks new work', async () => {
  const h = harness({ start: async () => { throw Error('lost response'); },
    cancelRequest: async (_agent, command) => requestState(command, { stop_status: 'requested', report: { ...report(command), status: 'unknown', result_digest: null, completed_at: null, error_code: 'result_unknown' } }),
    requestState: async () => {
      const command = h.calls.find(call => call.name === 'start').args[1];
      return requestState(command, { stop_status: 'confirmed', report: { ...report(command), status: 'unknown', result_digest: null, completed_at: null, error_code: 'result_unknown' } });
    },
  });
  await opened(h); await h.store.getState().start({}, { ready: true }); await h.store.getState().cancel();
  assert.equal(h.store.getState().requestState.stop_status, 'requested'); assert.equal(h.store.getState().unknown, true);
  await h.store.getState().start({}, { ready: true }); assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
  await h.store.getState().recover(); assert.equal(h.store.getState().unknown, false);
  assert.equal(h.store.getState().report.status, 'unknown');
});

test('a late preparation read cannot overwrite a confirmed cancellation', async () => {
  const oldRead = deferred();
  const h = harness({ start: async () => { throw Error('lost response'); }, requestState: () => oldRead.promise,
    cancelRequest: async (_agent, command) => requestState(command, { status: 'cancelled', stop_status: 'confirmed', report: null, error_code: 'cancelled' }),
  });
  await opened(h); await h.store.getState().start({}, { ready: true });
  const check = h.store.getState().recover(); await tick();
  await h.store.getState().cancel();
  const command = h.calls.find(call => call.name === 'start').args[1];
  oldRead.resolve(requestState(command, { status: 'preparing', stop_status: 'none', completed_at: null, report: null }));
  await check;
  assert.equal(h.store.getState().requestState.status, 'cancelled');
  assert.equal(h.store.getState().unknown, false); assert.equal(h.store.getState().checking, false);
});
