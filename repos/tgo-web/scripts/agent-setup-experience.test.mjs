import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture({ saveResult = true } = {}) {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  let finish;
  const calls = [];
  const draft = { templates: [{ id: 'knowledge', module: 'agents' }], step: 'business', current: null, busy: false,
    save: input => { calls.push(input); return new Promise(resolve => { finish = () => resolve(saveResult); }); } };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/agentSetupExperience.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, require: name => {
    if (name === '@/stores/authStore') return { useAuthStore: { getState: () => ({ user }) } };
    if (name === '@/stores/scenarioDraftStore') return { useScenarioDraftStore: { getState: () => draft } };
    if (name === '@/stores/scenarioResourceStore') return { useScenarioResourceStore: { getState: () => ({ channel: 'web' }) } };
    if (name === '@/services/aiAgentsApi') return { SYSTEM_DEFAULT_MODEL: '__system_default__' };
    if (name === './agentScenarioForm') return { agentDraftInput: (form, template, channel, step) => ({ form, template, channel, step }) };
    throw Error(`Unexpected dependency: ${name}`);
  } });
  return { ...exports, draft, calls, finish: () => finish(), changeUser: next => { user = next; } };
}

const form = (changes = {}) => ({ scenarioId: 'knowledge', llmModel: '__system_default__', tools: [], workflows: [], boundDeviceId: '', ...changes });

test('ordinary knowledge setup keeps extra resources folded; saved selections remain discoverable', () => {
  const h = fixture();
  assert.equal(h.shouldExpandAgentResources(form()), false);
  assert.equal(h.shouldExpandAgentResources(form({ scenarioId: 'aftersales' })), false);
  for (const selection of [{ tools: ['tool-a'] }, { workflows: ['workflow-a'] }, { boundDeviceId: 'device-a' }, { llmModel: 'provider:model' }]) {
    assert.equal(h.shouldExpandAgentResources(form(selection)), true);
  }
  assert.equal(h.shouldExpandAgentResources(form({ scenarioId: 'logistics' })), true);
  assert.equal(h.shouldExpandAgentResources(form({ scenarioId: 'assistant' })), true);
});

test('saving for later preserves the current step and waits for confirmation', async () => {
  const h = fixture();
  const pending = h.saveAgentSetupDraft(form());
  assert.equal(h.calls[0].step, 'business');
  assert.equal(h.calls[0].channel, 'web');
  h.finish();
  assert.equal(await pending, true);
});

test('an unconfirmed draft save does not allow closing', async () => {
  const h = fixture({ saveResult: false });
  const pending = h.saveAgentSetupDraft(form());
  h.finish();
  assert.equal(await pending, false);
});

test('a late save cannot close a different staff session or one whose role changed', async () => {
  for (const next of [{ id: 'staff-b', project_id: 'project-a', role: 'admin' }, { id: 'staff-a', project_id: 'project-a', role: 'user' }]) {
    const h = fixture();
    const pending = h.saveAgentSetupDraft(form());
    h.changeUser(next); h.finish();
    assert.equal(await pending, false);
  }
});

test('read only staff, a busy store and a missing template cannot dispatch a draft write', async () => {
  for (const setup of [h => h.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'user' }), h => { h.draft.busy = true; }, h => { h.draft.templates = []; }]) {
    const h = fixture(); setup(h);
    assert.equal(await h.saveAgentSetupDraft(form()), false);
    assert.equal(h.calls.length, 0);
  }
});
