import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function harness() {
  let identity = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const authListeners = [];
  const resourceListeners = [];
  const pending = [];
  const calls = [];
  const catalogue = {
    project_id: 'project-a', channel: 'web', checked_at: '2026-10-12T10:00:00Z',
    sections: [
      { kind: 'model', status: 'available', items: [resource('model', 'model-a', { is_default: true, purpose: 'chat', provider_id: 'provider-a', model_id: 'chat-a' })] },
      { kind: 'knowledge', status: 'available', items: [resource('knowledge', 'knowledge-a')] },
    ],
  };
  let resources = { catalog: catalogue, channel: 'web', loading: false, failed: false };
  const auth = { getState: () => ({ user: identity }), subscribe: callback => authListeners.push(callback) };
  const resourceStore = { getState: () => resources, subscribe: callback => resourceListeners.push(callback) };
  const create = initializer => {
    let state;
    const set = change => { state = { ...state, ...change }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const template = { id: 'knowledge', module: 'agents', version: 1, available: true, required_resources: ['model', 'knowledge'], default_model_mode: 'system_default' };
  const api = {
    templates: async module => sceneTemplates(module),
    readiness: request => {
      calls.push(request);
      return new Promise((resolve, reject) => pending.push({ resolve, reject, request }));
    },
  };
  const cache = {};
  function load(path) {
    if (cache[path]) return cache[path];
    const exports = {};
    cache[path] = exports;
    const source = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText;
    vm.runInNewContext(source, { exports, Promise, require: name => {
      if (name === 'zustand') return { create };
      if (name === './authStore') return { useAuthStore: auth };
      if (name === './scenarioResourceStore') return { useScenarioResourceStore: resourceStore };
      if (name === '@/services/businessScenariosApi') return { BusinessScenariosApi: api };
      if (name === '@/services/aiAgentsApi') return { SYSTEM_DEFAULT_MODEL: '__system_default__' };
      if (name === '@/utils/scenarioPreparation') return load('../src/utils/scenarioPreparation.ts');
      if (name === './businessScenarios' || name === '@/utils/businessScenarios') return load('../src/utils/businessScenarios.ts');
      throw new Error(`Unexpected import: ${name}`);
    } });
    return exports;
  }
  const utils = load('../src/utils/scenarioPreparation.ts');
  const { useScenarioPreparationStore: store } = load('../src/stores/scenarioPreparationStore.ts');
  return {
    store, utils, calls, api, template, catalogue,
    async begin(module = 'agents') { const promise = store.getState().load(module); await Promise.resolve(); await Promise.resolve(); return { promise }; },
    resolve(overrides = {}) {
      const item = pending.shift();
      item.resolve({ project_id: 'project-a', channel: item.request.channel, template_id: item.request.template_id, template_version: 1,
        configuration_ready: true, can_enable: false, test_status: 'not_tested', issues: [], effective_bindings: item.request.bindings, ...overrides });
    },
    reject() { pending.shift().reject(new Error('Unavailable')); },
    changeIdentity(next) { const previous = { user: identity }; identity = next; authListeners.forEach(listener => listener({ user: next }, previous)); },
    changeResources(next) { const previous = resources; resources = { ...resources, ...next }; resourceListeners.forEach(listener => listener(resources, previous)); },
  };
}

function resource(kind, id, details = {}) {
  return { kind, id, status: 'configured', name: id, details };
}

function sceneTemplates(module) {
  const ids = { agents: ['knowledge', 'logistics', 'aftersales', 'assistant'], workflows: ['knowledge', 'logistics', 'aftersales'], devices: ['files', 'report', 'screen'] }[module];
  return ids.map(id => ({ id, module, version: 1, available: id === 'knowledge', required_resources: ['model', 'knowledge'], default_model_mode: 'system_default' }));
}

test('a unique current knowledge resource is reused without copying the system default model', () => {
  const fixture = harness();
  const result = fixture.utils.suggestScenarioBindings(fixture.template, fixture.catalogue);
  assert.equal(result.model, null, 'keep following the system model rather than fixing today\'s model');
  assert.deepEqual(Array.from(result.knowledge_ids), ['knowledge-a']);
  assert.equal(result.tool_ids.length, 0);
});

test('multiple, partial, offline and unready resources require an explicit choice', () => {
  const fixture = harness();
  const section = fixture.catalogue.sections[1];
  for (const candidate of [
    { ...section, items: [...section.items, resource('knowledge', 'knowledge-b')] },
    { ...section, status: 'partial' },
    { ...section, items: [{ ...section.items[0], status: 'not_ready' }] },
    { kind: 'knowledge', status: 'unavailable', items: [] },
  ]) {
    assert.equal(fixture.utils.suggestScenarioBindings(fixture.template, { ...fixture.catalogue, sections: [candidate] }).knowledge_ids.length, 0);
  }
});

test('the default model remains dynamic when owner-approved resources enter the employee form', () => {
  const fixture = harness();
  const bindings = fixture.utils.suggestScenarioBindings(fixture.template, fixture.catalogue);
  const result = fixture.utils.preparationAgentResources({ effective_bindings: { ...bindings, model: { provider_id: 'provider-a', model_id: 'chat-a' } } }, fixture.catalogue);
  assert.equal(result.llmModel, '__system_default__');
  assert.deepEqual(Array.from(result.knowledgeBases), ['knowledge-a']);
  assert.equal(result.boundDeviceId, '');
  result.knowledgeBases.push('changed-by-form');
  assert.deepEqual(Array.from(bindings.knowledge_ids), ['knowledge-a']);
});

test('one configured chat model can be reused when no default exists but multiple models need choice', () => {
  const fixture = harness();
  const model = { ...fixture.catalogue.sections[0].items[0], details: { ...fixture.catalogue.sections[0].items[0].details, is_default: false } };
  const catalogue = { ...fixture.catalogue, sections: [{ kind: 'model', status: 'available', items: [model] }] };
  assert.equal(fixture.utils.suggestScenarioBindings(fixture.template, catalogue).model.model_id, 'chat-a');
  catalogue.sections[0].items.push({ ...model, id: 'model-b', details: { ...model.details, model_id: 'chat-b' } });
  assert.equal(fixture.utils.suggestScenarioBindings(fixture.template, catalogue).model, null);
});

test('template scope, duplicates and unknown resource kinds cannot produce preparation', async () => {
  for (const fault of ['module', 'duplicate', 'resource']) {
    const fixture = harness();
    fixture.api.templates = async () => {
      const templates = sceneTemplates('agents');
      if (fault === 'module') templates[0].module = 'devices';
      if (fault === 'duplicate') templates.push(templates[0]);
      if (fault === 'resource') templates[0].required_resources = ['external-tenant'];
      return templates;
    };
    await fixture.store.getState().load('agents');
    assert.equal(fixture.store.getState().modules.agents.failed, true);
    assert.equal(fixture.calls.length, 0);
  }
});

test('the scene card requests actual owner checks and only prepares configuration', async () => {
  const fixture = harness();
  const { promise } = await fixture.begin();
  assert.equal(fixture.calls.length, 1);
  assert.deepEqual(Array.from(fixture.calls[0].bindings.knowledge_ids), ['knowledge-a']);
  fixture.resolve(); await promise;
  const entry = fixture.store.getState().entries['agents:knowledge'];
  assert.equal(entry.status, 'checked');
  assert.equal(entry.report.can_enable, false);
  assert.equal(entry.report.test_status, 'not_tested');
  await fixture.store.getState().load('agents');
  assert.equal(fixture.calls.length, 1, 'same catalogue snapshot shares its check');
});

for (const boundary of ['account', 'company', 'role', 'channel', 'refresh']) {
  test(`late scene results cannot cross a ${boundary} boundary`, async () => {
    const fixture = harness();
    const { promise } = await fixture.begin();
    if (boundary === 'account') fixture.changeIdentity({ id: 'staff-b', project_id: 'project-a', role: 'admin' });
    if (boundary === 'company') fixture.changeIdentity({ id: 'staff-a', project_id: 'project-b', role: 'admin' });
    if (boundary === 'role') fixture.changeIdentity({ id: 'staff-a', project_id: 'project-a', role: 'user' });
    if (boundary === 'channel') fixture.changeResources({ channel: 'wecom_kf', catalog: null });
    if (boundary === 'refresh') fixture.changeResources({ loading: true });
    fixture.resolve(); await promise;
    assert.equal(fixture.store.getState().entries['agents:knowledge'], undefined);
  });
}

for (const fault of ['project', 'channel', 'template', 'version', 'enable', 'ready', 'route']) {
  test(`malformed ${fault} response is unavailable rather than configured`, async () => {
    const fixture = harness();
    const { promise } = await fixture.begin();
    const overrides = {
      project: { project_id: 'project-b' }, channel: { channel: 'phone' }, template: { template_id: 'files' },
      version: { template_version: 2 }, enable: { can_enable: true }, ready: { configuration_ready: 'true' },
      route: { configuration_ready: false, issues: [{ kind: 'knowledge', code: 'missing', message: '请选择资料', action: 'configure_resource', route: 'https://external.invalid' }] },
    };
    fixture.resolve(overrides[fault]); await promise;
    assert.equal(fixture.store.getState().entries['agents:knowledge'].status, 'failed');
    assert.equal(fixture.store.getState().entries['agents:knowledge'].report, null);
  });
}

test('configuration issues remain actionable and source errors are not missing resources', async () => {
  const fixture = harness();
  const { promise } = await fixture.begin();
  const issue = { kind: 'knowledge', code: 'unavailable', message: '暂时无法核验', action: 'retry', route: '/knowledge' };
  fixture.resolve({ configuration_ready: false, issues: [issue] }); await promise;
  assert.equal(fixture.store.getState().entries['agents:knowledge'].report.issues[0].code, 'unavailable');
  fixture.changeResources({ catalog: { ...fixture.catalogue, checked_at: '2026-10-12T11:00:00Z' } });
  const second = await fixture.begin(); fixture.reject(); await second.promise;
  assert.equal(fixture.store.getState().entries['agents:knowledge'].status, 'failed');
});

test('unavailable templates do not request or imply executable screen capability', async () => {
  const fixture = harness();
  fixture.api.templates = async () => sceneTemplates('devices').map(template => ({ ...template, available: false, required_resources: ['device'] }));
  await fixture.store.getState().load('devices');
  assert.equal(fixture.calls.length, 0);
  assert.equal(fixture.store.getState().entries['devices:screen'].status, 'planned');
});
