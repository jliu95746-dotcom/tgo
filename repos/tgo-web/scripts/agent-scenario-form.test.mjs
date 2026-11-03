import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/agentScenarioForm.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports, require: () => ({ SYSTEM_DEFAULT_MODEL: 'system-default' }) });
const { agentDraftInput, formFromAgentDraft, agentStepProblem, initialRoleSettings } = exports;
const form = () => ({ scenarioId: 'knowledge', name: '澜月客服', profession: '咨询', description: '依据资料回答', companySummary: '澜月女包企业介绍',
  llmModel: 'system-default', knowledgeBases: ['kb-a'], tools: [], workflows: [], boundDeviceId: '', toolConfigs: {},
  markdown: false, skills_enabled: false, tool_call_limit: 3, num_history_runs: 0,
});
const plain = data => JSON.parse(JSON.stringify(data));

test('new knowledge roles start without unverified ordinary skills', () => {
  assert.deepEqual(plain(initialRoleSettings('knowledge', { skills_enabled: true })), { skills_enabled: false });
  assert.deepEqual(plain(initialRoleSettings('aftersales', { skills_enabled: true })), { skills_enabled: false });
});
test('role changes and other roles retain user-selected advanced settings', () => {
  assert.deepEqual(plain(initialRoleSettings('knowledge', { scenarioId: 'logistics', skills_enabled: true })), {});
  assert.deepEqual(plain(initialRoleSettings('assistant', { skills_enabled: true })), {});
});

test('forward navigation requires business details and a valid model while previous edits are retained', () => {
  assert.equal(agentStepProblem('scenario', { ...form(), scenarioId: undefined }), 'chooseRole');
  assert.equal(agentStepProblem('business', { ...form(), name: '' }), 'completeBusiness');
  assert.equal(agentStepProblem('business', { ...form(), companySummary: '' }), 'completeCompany');
  assert.equal(agentStepProblem('resources', { ...form(), llmModel: 'invalid-model' }), 'chooseModel');
  assert.equal(agentStepProblem('resources', form()), null);
});

test('draft round trip preserves company, model mode, resources and non-default advanced preferences', () => {
  const input = agentDraftInput(form(), { id: 'knowledge', version: 1 }, 'web', 'testing', null);
  const restored = formFromAgentDraft({ ...input, id: 'draft-a' });
  assert.equal(input.step, 'testing');
  assert.equal(input.bindings.model, null);
  for (const key of ['name', 'profession', 'description', 'companySummary', 'llmModel', 'knowledgeBases', 'markdown', 'skills_enabled', 'tool_call_limit', 'num_history_runs']) {
    assert.deepEqual(plain(restored[key]), plain(form()[key]));
  }
});

test('custom model and original template version survive continued editing', () => {
  const input = agentDraftInput({ ...form(), llmModel: 'provider-a:model:variant' }, { id: 'knowledge', version: 2 }, 'web', 'activation', {
    template_version: 1, bindings: { connection_ids: ['connection-a'] },
  });
  assert.equal(input.template_version, 1);
  assert.equal(input.bindings.model.model_id, 'model:variant');
  assert.deepEqual(plain(input.bindings.connection_ids), ['connection-a']);
});

test('a draft never silently discards configured tool parameters or persists secrets', () => {
  assert.throws(() => agentDraftInput({ ...form(), toolConfigs: { tool: { api_key: 'fixture-secret' } } }, { id: 'knowledge', version: 1 }, 'web', 'resources', null), /toolConfig/);
});
