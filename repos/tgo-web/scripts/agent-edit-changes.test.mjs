import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/agentEditChanges.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports });
const patch = (form, original, config) => JSON.parse(JSON.stringify(exports.agentEditChanges(form, original, config)));
const original = { name: '澜月客服', description: '咨询', profession: '客服', llmModel: '__system_default__',
  tools: ['tool-a'], toolConfigs: { 'tool-a': { choice: 'current', limit: 3 } }, knowledgeBases: ['kb-a'], workflows: ['flow-a'], boundDeviceId: 'device-a',
  markdown: true, add_datetime_to_context: true, tool_call_limit: 10, num_history_runs: 5,
  skills_enabled: false, humanization_skill_name: null, humanization_skill_enabled: false };

test('unchanged edit form sends no business fields', () => {
  assert.deepEqual(patch(structuredClone(original), original, { extra: 'retained' }), {});
});
test('nickname-only editing preserves all model, resource and business settings', () => {
  assert.deepEqual(patch({ ...original, name: '澜月咨询客服' }, original), { name: '澜月咨询客服' });
});
test('advanced config changes retain unknown existing server settings', () => {
  assert.deepEqual(patch({ ...original, tool_call_limit: 2 }, original, { markdown: true, tool_call_limit: 10, extra: { retained: true } }),
    { config: { markdown: true, tool_call_limit: 2, extra: { retained: true } } });
});
test('device removal is represented explicitly without clearing workflows', () => {
  assert.deepEqual(patch({ ...original, boundDeviceId: '' }, original), { boundDeviceId: '' });
});
test('tool settings compare object contents instead of insertion order', () => {
  const next = { ...original, toolConfigs: { 'tool-a': { limit: 3, choice: 'current' } } };
  assert.deepEqual(patch(next, original), {});
  assert.deepEqual(patch({ ...next, toolConfigs: { 'tool-a': { limit: 4 } } }, original), { toolConfigs: { 'tool-a': { limit: 4 } } });
});
test('a selected system model or humanization setting is sent only when changed', () => {
  assert.deepEqual(patch({ ...original, llmModel: 'provider:model', humanization_skill_name: 'friendly' }, original),
    { llmModel: 'provider:model', humanization_skill_name: 'friendly' });
});
