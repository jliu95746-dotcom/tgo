import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const exports = {};
const source = readFileSync(new URL('../src/utils/sessionClosedWorkflowTemplate.ts', import.meta.url), 'utf8');
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
} }).outputText, { exports, crypto: { randomUUID }, require: name => {
  if (name === '@/i18n') return { t: key => key };
  throw Error(`Unexpected dependency ${name}`);
} });

test('template creates an internal event summary draft with unique edges and no customer action', () => {
  const draft = exports.buildSessionClosedWorkflow('  会话片段  ');
  assert.equal(draft.name, '会话片段');
  assert.deepEqual(Array.from(draft.nodes, node => node.type), ['event', 'llm', 'answer']);
  assert.equal(draft.nodes[0].data.event_type, 'staff.session.closed');
  assert.equal(draft.nodes[1].data.user_prompt.includes('{{event.data}}'), true);
  assert.equal(draft.nodes[2].data.output_variable, 'summary.text');
  assert.equal(draft.nodes[1].data.provider_id, undefined);
  assert.equal(draft.nodes[1].data.tool_ids?.length ?? 0, 0);
  assert.equal(draft.status, undefined);
  draft.edges.forEach((edge, index) => {
    assert.equal(edge.source, draft.nodes[index].id);
    assert.equal(edge.target, draft.nodes[index + 1].id);
  });
  const another = exports.buildSessionClosedWorkflow('另一草稿');
  assert.ok(another.nodes.every(node => !draft.nodes.some(old => old.id === node.id)));
});

test('selected model is explicit and the prompt does not invent full-session facts or actions', () => {
  const draft = exports.buildSessionClosedWorkflow('摘要', {
    provider_id: 'tenant-provider', model_id: 'tenant-model', model_name: 'configured-model',
  });
  const llm = draft.nodes[1].data;
  assert.equal(llm.provider_id, 'tenant-provider');
  assert.equal(llm.model_id, 'tenant-model');
  assert.equal(llm.model_name, 'configured-model');
  assert.match(llm.system_prompt, /不可信/);
  assert.match(llm.system_prompt, /片段/);
  assert.match(llm.system_prompt, /缺失/);
  assert.match(llm.system_prompt, /不得.*发送/);
  assert.throws(() => exports.buildSessionClosedWorkflow('  '));
});
