import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createStore } from 'zustand/vanilla';

const source = readFileSync(new URL('../src/utils/agentReadiness.ts', import.meta.url), 'utf8');
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, context);
const { getAgentReadiness } = context.exports;
test('default chat follows the latest persisted list state in both directions', () => {
  const enabled = context.exports.isDefaultAgentEnabled;
  assert.equal(enabled(null, []), false);
  assert.equal(enabled({ id: 'a', is_active: true }, [{ id: 'a', status: 'inactive' }]), false);
  assert.equal(enabled({ id: 'a', is_active: false }, [{ id: 'a', status: 'active' }]), true);
  assert.equal(enabled({ id: 'a', is_active: false }, [{ id: 'b', status: 'active' }]), false);
  assert.equal(enabled({ id: 'a', is_active: true }, []), true);
  const page = readFileSync(new URL('../src/components/ai/AgentManagement.tsx', import.meta.url), 'utf8');
  assert.match(page, /isDefaultAgentEnabled\(defaultAgent, agents\)/);
  assert.match(page, /disabled=\{!defaultAgentEnabled \|\| isLoadingDefaultAgent\}/);
});
const models = [{ provider_id: 'provider-a', model_id: 'chat:v1', model_type: 'chat', is_active: true }];
for (const [name, value, available, state, expected] of [
  ['missing model is not an invented default', '', models, 'loaded', 'missing_model'],
  ['loading is not unavailable', 'provider-a:chat:v1', [], 'loading', 'checking'],
  ['request failure is not empty configuration', 'provider-a:chat:v1', [], 'error', 'check_failed'],
  ['exact provider and colon-containing model', 'provider-a:chat:v1', models, 'loaded', 'configured'],
  ['wrong provider cannot borrow a matching name', 'provider-b:chat:v1', models, 'loaded', 'unavailable_model'],
  ['disabled model', 'provider-a:chat:v1', [{ ...models[0], is_active: false }], 'loaded', 'unavailable_model'],
  ['embedding is not a chat model', 'provider-a:chat:v1', [{ ...models[0], model_type: 'embedding' }], 'loaded', 'unavailable_model'],
  ['legacy model requires provider confirmation', 'gpt-4o', [{ ...models[0], model_id: 'gpt-4o' }], 'loaded', 'provider_unassigned'],
  ['no configured provider', 'gpt-4o', [], 'loaded', 'unavailable_model'],
]) {
  test(name, () => assert.equal(getAgentReadiness(value, available, state), expected));
}

const storeSource = readFileSync(new URL('../src/stores/agentReadinessStore.ts', import.meta.url), 'utf8');
function makeStore(listProjectModels) {
  const scope = { exports: {}, require(name) {
    if (name === 'zustand') return { create: createStore };
    if (name === '@/services/aiProvidersApi') return { default: class {
      listProjectModels = listProjectModels;
    } };
    throw new Error(`Unexpected import ${name}`);
  } };
  vm.runInNewContext(ts.transpileModule(storeSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, scope);
  return scope.exports.useAgentReadinessStore;
}
const page = (data, has_next = false) => ({ data, pagination: { has_next } });

test('all pages load; cached checks are refreshed explicitly', async () => {
  const offsets = [];
  const store = makeStore(async ({ offset }) => {
    offsets.push(offset);
    return offset === 0 ? page(models, true) : page([{ ...models[0], model_id: 'second' }]);
  });
  await store.getState().load('a');
  assert.deepEqual(offsets, [0, 1]);
  assert.equal(store.getState().models.length, 2);
  await store.getState().load('a');
  assert.equal(offsets.length, 2);
  await store.getState().load('a', true);
  assert.deepEqual(offsets, [0, 1, 0, 1]);
});

test('failed checks remove old results and permit retry', async () => {
  let fail = false;
  const store = makeStore(async () => {
    if (fail) throw Error('offline');
    return page(models);
  });
  await store.getState().load('a');
  fail = true;
  await store.getState().load('a', true);
  assert.equal(store.getState().status, 'error');
  assert.equal(store.getState().models.length, 0);
  fail = false;
  await store.getState().load('a');
  assert.equal(store.getState().status, 'loaded');
});

test('late response from another project cannot overwrite current project', async () => {
  let resolveFirst;
  let requests = 0;
  const store = makeStore(() => ++requests === 1
    ? new Promise(resolve => { resolveFirst = resolve; }) : Promise.resolve(page([])));
  const first = store.getState().load('a');
  await store.getState().load('a');
  assert.equal(requests, 1);
  await store.getState().load('b');
  resolveFirst(page(models));
  await first;
  assert.equal(store.getState().projectId, 'b');
  assert.equal(store.getState().models.length, 0);
});

test('empty nonterminal page fails instead of looping or reporting complete', async () => {
  const store = makeStore(async () => page([], true));
  await store.getState().load('a');
  assert.equal(store.getState().status, 'error');
});

test('card uses readiness evidence, no invented model; both locales include all states', () => {
  const card = readFileSync(new URL('../src/components/ai/AgentCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /getAgentReadiness\(agent\.llmModel/);
  assert.doesNotMatch(card, /gemini-1\.5-pro/);
  assert.match(card, /agents\.card\.readinessNotice/);
  for (const language of ['zh', 'en']) {
    const translations = JSON.parse(readFileSync(new URL(`../src/i18n/locales/${language}.json`, import.meta.url), 'utf8'));
    for (const state of ['checking', 'check_failed', 'missing_model', 'unavailable_model', 'provider_unassigned', 'configured']) {
      assert.ok(translations.agents.card.readiness[state]);
    }
  }
});
