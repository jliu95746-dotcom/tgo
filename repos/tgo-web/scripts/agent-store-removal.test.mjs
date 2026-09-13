import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');

test('employee management retains manual creation but has no recruitment entry', () => {
  const page = read('components/ai/AgentManagement.tsx');
  assert.doesNotMatch(page, /AgentStoreModal|showAgentStore|agents\.actions\.store/);
  assert.match(page, /<CreateAgentModal/);
  assert.match(page, /<EditAgentModal/);
  assert.match(page, /onClick=\{handleCreateAgent\}/);
});

test('recruitment-only components and types are removed', () => {
  for (const component of ['AgentStoreCard', 'AgentStoreDetail', 'AgentStoreModal', 'AgentDependencyModal']) {
    assert.equal(existsSync(new URL(`../src/components/ai/${component}.tsx`, import.meta.url)), false);
  }
  assert.doesNotMatch(read('types/index.ts'), /AgentStoreItem|AgentStoreCategory|AgentDependencyCheckResponse|StoreToolSummary/);
  assert.doesNotMatch(read('components/ai/store/types.ts'), /AgentStoreCategory|'agent'/);
});

test('store API keeps model and tool installation, not employee recruitment', () => {
  const api = read('services/storeApi.ts');
  assert.doesNotMatch(api, /getAgentCategories|checkAgentDependencies|installAgent|uninstallAgent|getAgents:|getAgent:/);
  assert.doesNotMatch(api, /installTool:/);
  assert.match(api, /installModel:/);
  assert.match(api, /getStoreConfig:/);
});

test('both locales drop only employee-store copy', () => {
  for (const language of ['zh', 'en']) {
    const locale = JSON.parse(read(`i18n/locales/${language}.json`));
    assert.equal(locale.agents.actions.store, undefined);
    assert.equal(locale.agents.modal.store, undefined);
    assert.ok(locale.agents.actions.create);
  }
});
