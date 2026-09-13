import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/aiAgentsApi.ts', import.meta.url), 'utf8');
const scope = { exports: {}, require(name) {
  if (name === './base/BaseApiService') return { default: class {} };
  throw Error(`Unexpected dependency: ${name}`);
} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, scope);
const transform = scope.exports.AIAgentsTransformUtils;
const agent = { id: 'test-agent', name: '验收员工', model: 'chat:v1',
  updated_at: '2026-09-10T00:00:00Z', tools: [], collections: [] };

test('system-follow mode survives load and clears explicit credentials when saved', () => {
  const loaded = transform.transformApiAgentToAgent({ ...agent, model: '__system_default__', llm_provider_id: 'old-provider' });
  assert.equal(loaded.llmModel, '__system_default__');
  const request = transform.transformAgentToUpdateRequest(loaded);
  assert.equal(request.model, '__system_default__');
  assert.equal(request.ai_provider_id, null);
});

test('employee humanization binding survives response and update independently of ordinary skills', () => {
  const loaded = transform.transformApiAgentToAgent({ ...agent,
    humanization_skill_name: 'brand-style', humanization_skill_enabled: true, skills_enabled: false });
  const saved = transform.transformAgentToUpdateRequest(loaded);
  assert.equal(saved.humanization_skill_name, 'brand-style');
  assert.equal(saved.humanization_skill_enabled, true);
  assert.equal(saved.skills_enabled, false);
  const disabled = transform.transformAgentPatch(loaded, { humanization_skill_enabled: false });
  assert.equal(disabled.humanization_skill_name, 'brand-style');
  assert.equal(disabled.humanization_skill_enabled, false);
  const cleared = transform.transformAgentPatch(loaded, { humanization_skill_name: null, humanization_skill_enabled: false });
  assert.equal(cleared.humanization_skill_name, null);
});

for (const [name, fields, expected] of [
  ['canonical response provider survives list transformation', { llm_provider_id: 'provider-a' }, 'provider-a:chat:v1'],
  ['legacy response alias is accepted', { ai_provider_id: 'legacy' }, 'legacy:chat:v1'],
  ['canonical provider wins when aliases conflict', { llm_provider_id: 'provider-a', ai_provider_id: 'legacy' }, 'provider-a:chat:v1'],
  ['missing provider is not invented', {}, 'chat:v1'],
]) {
  test(name, () => assert.equal(transform.transformApiAgentToAgent({ ...agent, ...fields }).llmModel, expected));
}

test('saving a loaded employee preserves the actual provider and colon-containing model', () => {
  const loaded = transform.transformApiAgentToAgent({ ...agent, llm_provider_id: 'provider-a' });
  const request = transform.transformAgentToUpdateRequest(loaded);
  assert.equal(request.ai_provider_id, 'provider-a');
  assert.equal(request.model, 'chat:v1');
});

test('disabled API state survives refresh and ordinary edits', () => {
  const loaded = transform.transformApiAgentToAgent({ ...agent, is_active: false });
  assert.equal(loaded.status, 'inactive');
  assert.equal(transform.transformAgentToUpdateRequest(loaded).is_active, false);
});

test('status-only patch never rewrites resources or model configuration', () => {
  const loaded = transform.transformApiAgentToAgent({ ...agent, llm_provider_id: 'provider-a' });
  assert.equal(JSON.stringify(transform.transformAgentPatch(loaded, { status: 'inactive' })), '{"is_active":false}');
  assert.equal(JSON.stringify(transform.transformAgentPatch(loaded, { status: 'active' })), '{"is_active":true}');
});
