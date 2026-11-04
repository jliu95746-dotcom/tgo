import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function harness(saved = new Map()) {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const requests = [], listeners = [], calls = [];
  const create = initializer => {
    let state;
    const set = next => { state = { ...state, ...(typeof next === 'function' ? next(state) : next) }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const operation = name => (...args) => {
    calls.push({ name, args });
    return new Promise((resolve, reject) => requests.push({ name, resolve, reject }));
  };
  const exports = {};
  const businessValidation = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/agentBusinessTrialValidation.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: businessValidation });
  const helper = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/activationRequestRecovery.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: helper, localStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key) } });
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/agentActivationStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, AbortController, Map, Date, Set, crypto: { randomUUID: () => '10000000-0000-4000-8000-000000000001' }, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: { getState: () => ({ user }), subscribe: fn => listeners.push(fn) } };
    if (name === '@/services/agentActivationPoliciesApi') return { AgentActivationPoliciesApi: {
      read: operation('policies'), readiness: operation('readiness'), save: operation('save'), remove: operation('remove'),
    } };
    if (name === '@/services/businessScenariosApi') return { BusinessScenariosApi: { templates: operation('templates') } };
    if (name === '@/utils/activationRequestRecovery') return helper;
    if (name === '@/utils/agentBusinessTrialValidation') return businessValidation;
    if (name === '@/services/agentActivationRequestsApi') return { AgentActivationRequestsApi: { submit: operation('activation'), lookup: operation('lookup') } };
    throw new Error(name);
  } });
  return { store: exports.useAgentActivationStore, calls, saved,
    resolve: (name, value) => { const index = requests.findIndex(row => row.name === name); assert.ok(index >= 0, name); requests.splice(index, 1)[0].resolve(value); },
    reject: (name, status) => { const index = requests.findIndex(row => row.name === name); requests.splice(index, 1)[0].reject(Object.assign(new Error('failed'), { status })); },
    change: next => { const previous = { user }; user = next; listeners.forEach(fn => fn({ user }, previous)); },
  };
}

function policies() {
  return { project_id: 'project-a', agent_id: 'agent-a', can_enable: false, policies: [{
    id: 'policy-a', template_id: 'knowledge', template_version: 1, channel: 'web', revision: 1,
    requirements_revision: 'a'.repeat(64), configured_by: 'staff-a', enabled: true,
  }] };
}
function readiness(can = true) {
  return { project_id: 'project-a', agent_id: 'agent-a', checked_by: 'staff-a', checked_at: new Date().toISOString(),
    scope: 'employee_activation', enabled: false, can_enable: can, configuration_fingerprint: 'b'.repeat(64),
    model_owner_revision: 'c'.repeat(64), blockers: can ? [] : [{ code: 'tests_not_current', channel: 'web', resource: 'knowledge', message: '需要重测' }],
    channels: [{ template_id: 'knowledge', template_version: 1, channel: 'web', policy_revision: 1,
      requirements_revision: 'a'.repeat(64), status: can ? 'passed' : 'needs_retest', valid_until: can ? new Date(Date.now() + 3600000).toISOString() : null }] };
}
async function loaded(f, can = true) {
  const promise = f.store.getState().load('agent-a');
  f.resolve('policies', policies()); f.resolve('readiness', readiness(can));
  await promise;
}
const selection = { template_id: 'aftersales', template_version: 1, channel: 'web' };
const flush = () => new Promise(resolve => setImmediate(resolve));
function activationReceipt(f, status = 'completed', changes = {}) {
  const command = f.calls.findLast(row => row.name === 'activation').args[1];
  return { scope: 'employee_activation_request', can_enable: false, project_id: 'project-a', requested_by: 'staff-a', agent_id: 'agent-a', request_key: command.request_key,
    status, command: status === 'not_found' ? null : command, finished_at: status === 'not_found' ? null : new Date().toISOString(),
    failure_code: status === 'rejected' ? 'configuration_changed' : null, ...changes };
}

test('template catalog deduplicates reads and uses the current server version', async () => {
  const f = harness();
  const first = f.store.getState().loadTemplates(), second = f.store.getState().loadTemplates();
  assert.equal(f.calls.length, 1);
  f.resolve('templates', [{ id: 'knowledge', module: 'agents', version: 7, available: true, title: '商品咨询客服' }]);
  await Promise.all([first, second]);
  assert.equal(f.store.getState().templatesReady, true);
  assert.equal(f.store.getState().templates[0].version, 7);
});

test('foreign or duplicate templates cannot become an available selection', async () => {
  const f = harness(), first = f.store.getState().loadTemplates();
  f.resolve('templates', [{ id: 'files', module: 'devices', version: 1, available: true }]);
  await first;
  assert.equal(f.store.getState().templatesReady, false);
  const second = f.store.getState().loadTemplates();
  const row = { id: 'knowledge', module: 'agents', version: 1, available: true, title: '商品咨询客服' };
  f.resolve('templates', [row, row]); await second;
  assert.equal(f.store.getState().templatesReady, false);
});

test('identity changes cancel and discard an old template catalog', async () => {
  const f = harness(), first = f.store.getState().loadTemplates();
  f.change({ id: 'staff-b', project_id: 'project-a', role: 'admin' });
  assert.equal(f.calls[0].args.at(-1).aborted, true);
  f.resolve('templates', [{ id: 'knowledge', module: 'agents', version: 1, available: true, title: '商品咨询客服' }]);
  await first;
  assert.equal(f.store.getState().templatesReady, false);
  assert.deepEqual(Array.from(f.store.getState().templates), []);
});

test('policy write receipt must belong to the current operator', async () => {
  const f = harness(); await loaded(f, false);
  const request = f.store.getState().save('agent-a', selection);
  f.resolve('save', { ...policies().policies[0], ...selection, revision: 2, configured_by: 'staff-b' });
  assert.equal(await request, false);
  assert.notEqual(f.store.getState().entries['agent-a'].pending, null);
});

test('one shared read is cached without granting an activation action', async () => {
  const f = harness(), promise = f.store.getState().load('agent-a');
  const duplicate = f.store.getState().load('agent-a');
  assert.equal(f.calls.length, 2);
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await Promise.all([promise, duplicate]);
  await f.store.getState().load('agent-a');
  assert.equal(f.calls.length, 2);
  assert.equal(f.store.getState().entries['agent-a'].readinessReady, true);
});

for (const fault of ['project', 'actor', 'scope', 'expired', 'missing_model', 'revision', 'channel', 'blockers', 'business_revision', 'business_without_operations']) {
  test(`invalid activation evidence ${fault} cannot appear ready`, async () => {
    const f = harness(), body = readiness();
    if (fault === 'project') body.project_id = 'other-project';
    if (fault === 'actor') body.checked_by = 'other-actor';
    if (fault === 'scope') body.scope = 'knowledge_templates';
    if (fault === 'expired') body.channels[0].valid_until = new Date(Date.now() - 1000).toISOString();
    if (fault === 'missing_model') body.model_owner_revision = null;
    if (fault === 'revision') body.channels[0].policy_revision = 2;
    if (fault === 'channel') body.channels[0].channel = 'wecom_kf';
    if (fault === 'blockers') body.blockers = [{ code: 'owner_unavailable', message: '无法核验' }];
    if (fault === 'business_revision') body.business_evidence_revision = 'invalid';
    if (fault === 'business_without_operations') body.channels[0].template_id = 'logistics';
    const promise = f.store.getState().load('agent-a');
    f.resolve('policies', policies()); f.resolve('readiness', body); await promise;
    assert.equal(f.store.getState().entries['agent-a'].readinessReady, false);
  });
}

test('read failure keeps history but removes usable readiness', async () => {
  const f = harness(); await loaded(f);
  const promise = f.store.getState().load('agent-a', true);
  f.resolve('policies', policies()); f.reject('readiness', 503); await promise;
  const entry = f.store.getState().entries['agent-a'];
  assert.equal(entry.policiesReady, true); assert.equal(entry.readinessReady, false);
});

for (const next of [null, { id: 'staff-a', project_id: 'project-a', role: 'user' }, { id: 'staff-b', project_id: 'project-a', role: 'admin' }]) {
  test(`identity transition ${next?.id ?? 'logout'} aborts and ignores old reads`, async () => {
    const f = harness(), promise = f.store.getState().load('agent-a');
    f.change(next);
    assert.ok(f.calls.every(call => call.args.at(-1)?.aborted));
    f.resolve('policies', policies()); f.resolve('readiness', readiness()); await promise;
    assert.equal(f.store.getState().identity, null);
    assert.equal(Object.keys(f.store.getState().entries).length, 0);
  });
}

test('unknown policy save retries its original revision and selection', async () => {
  const f = harness(); await loaded(f, false);
  const first = f.store.getState().save('agent-a', selection);
  f.reject('save', undefined); assert.equal(await first, false);
  const original = f.calls.find(call => call.name === 'save').args[1];
  assert.equal(original.expected_revision, 1);
  const retry = f.store.getState().retry('agent-a');
  assert.equal(JSON.stringify(f.calls.at(-1).args[1]), JSON.stringify(original));
  f.resolve('save', { ...policies().policies[0], ...selection, revision: 2 });
  await retry;
  assert.equal(f.store.getState().entries['agent-a'].pending, null);
  assert.equal(f.store.getState().entries['agent-a'].readinessReady, false);
});

test('an uncertain policy mutation cannot be overwritten by another choice', async () => {
  const f = harness(); await loaded(f, false);
  const first = f.store.getState().save('agent-a', selection); f.reject('save', undefined); await first;
  const count = f.calls.length;
  assert.equal(await f.store.getState().save('agent-a', { ...selection, template_id: 'knowledge' }), false);
  assert.equal(f.calls.length, count);
});

test('activation checks fresh evidence instead of using cached green status', async () => {
  const f = harness(); await loaded(f);
  let invoked = false;
  const request = f.store.getState().enable('agent-a', async () => { invoked = true; });
  f.resolve('policies', policies()); f.resolve('readiness', readiness(false));
  assert.equal(await request, false); assert.equal(invoked, false);
});

test('a confirmed enable action runs once and then invalidates previous checks', async () => {
  const f = harness(); await loaded(f);
  let count = 0;
  const request = f.store.getState().enable('agent-a', async () => { count++; });
  f.resolve('policies', policies()); f.resolve('readiness', readiness());
  await flush(); f.resolve('activation', activationReceipt(f));
  assert.equal(await request, true); assert.equal(count, 1);
  assert.equal(f.store.getState().entries['agent-a'].readinessReady, false);
});

test('role revocation while checking prevents the activation callback', async () => {
  const f = harness(); await loaded(f);
  let count = 0;
  const request = f.store.getState().enable('agent-a', async () => { count++; });
  f.change({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  f.resolve('policies', policies()); f.resolve('readiness', readiness());
  assert.equal(await request, false); assert.equal(count, 0);
});

test('a double click during fresh checking dispatches only one activation', async () => {
  const f = harness(); await loaded(f);
  let count = 0;
  const action = async () => { count++; };
  const first = f.store.getState().enable('agent-a', action);
  const second = f.store.getState().enable('agent-a', action);
  f.resolve('policies', policies()); f.resolve('readiness', readiness());
  await flush(); f.resolve('activation', activationReceipt(f));
  await Promise.all([first, second]);
  assert.equal(count, 1);
});

test('an unknown activation survives page reload and is only cleared by its original receipt', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await flush();
  const receipt = activationReceipt(f);
  f.reject('activation', 500); assert.equal(await request, false);
  assert.equal(f.saved.size, 1);
  const fresh = harness(f.saved); await loaded(fresh, false);
  assert.equal(fresh.store.getState().entries['agent-a'].pending.kind, 'enable');
  const lookup = fresh.store.getState().recover('agent-a'); fresh.resolve('lookup', receipt);
  assert.equal(await lookup, true); assert.equal(fresh.saved.size, 0);
  assert.equal(fresh.store.getState().entries['agent-a'].pending, null);
  assert.equal(fresh.store.getState().entries['agent-a'].readinessReady, false);
});

test('a current active flag cannot substitute for an original activation receipt', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await flush(); f.reject('activation', 500); await request;
  const check = f.store.getState().load('agent-a', true);
  f.resolve('policies', policies()); f.resolve('readiness', { ...readiness(), enabled: true }); await check;
  assert.equal(f.store.getState().entries['agent-a'].pending.kind, 'enable');
});

test('missing committed result permits only an unchanged original-number retry', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await flush(); f.reject('activation', 500); await request;
  const original = f.calls.find(row => row.name === 'activation').args[1];
  const lookup = f.store.getState().recover('agent-a'); f.resolve('lookup', activationReceipt(f, 'not_found')); await lookup;
  const retry = f.store.getState().retryActivation('agent-a', async () => {}); await flush();
  assert.equal(JSON.stringify(f.calls.at(-1).args[1]), JSON.stringify(original));
  f.resolve('activation', activationReceipt(f)); assert.equal(await retry, true);
});

test('receipt with another actor or confirmation never clears an unknown request', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await flush();
  f.resolve('activation', activationReceipt(f, 'completed', { requested_by: 'staff-b' }));
  assert.equal(await request, false); assert.equal(f.saved.size, 1);
});

test('corrupt stored intent cannot be overwritten by a fresh activation', async () => {
  const f = harness(); await loaded(f);
  f.saved.set('tgo.activation-request.v1:project-a:staff-a:agent-a', '{broken');
  const count = f.calls.length;
  assert.equal(await f.store.getState().enable('agent-a', async () => {}), false);
  assert.equal(f.calls.length, count);
  assert.equal(f.store.getState().entries['agent-a'].readinessReady, false);
});

test('terminal rejection clears only the original pending command and permits fresh checking', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await flush();
  f.resolve('activation', activationReceipt(f, 'rejected'));
  assert.equal(await request, false); assert.equal(f.saved.size, 0);
  assert.equal(f.store.getState().entries['agent-a'].pending, null);
  assert.equal(f.store.getState().entries['agent-a'].receipt.failure_code, 'configuration_changed');
});

test('an activation reply arriving after identity change remains isolated and recoverable by its owner', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies()); f.resolve('readiness', readiness()); await flush();
  f.change({ id: 'staff-b', project_id: 'project-a', role: 'admin' });
  f.resolve('activation', activationReceipt(f));
  assert.equal(await request, false); assert.equal(f.saved.size, 1);
  assert.equal(Object.keys(f.store.getState().entries).length, 0);
});

test('the original business evidence revision survives reload and cannot be replaced by another receipt', async () => {
  const f = harness(); await loaded(f);
  const request = f.store.getState().enable('agent-a', async () => {});
  f.resolve('policies', policies());
  f.resolve('readiness', { ...readiness(), business_evidence_revision: 'd'.repeat(64) });
  await flush();
  const original = activationReceipt(f);
  assert.equal(original.command.expected.business_evidence_revision, 'd'.repeat(64));
  f.reject('activation', 500); await request;
  const fresh = harness(f.saved); await loaded(fresh, false);
  const different = JSON.parse(JSON.stringify(original));
  different.command.expected.business_evidence_revision = 'e'.repeat(64);
  const changed = fresh.store.getState().recover('agent-a'); fresh.resolve('lookup', different);
  assert.equal(await changed, false); assert.equal(fresh.saved.size, 1);
  const recover = fresh.store.getState().recover('agent-a'); fresh.resolve('lookup', original);
  assert.equal(await recover, true); assert.equal(fresh.saved.size, 0);
});
