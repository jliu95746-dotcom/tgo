import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/ai/AgentManagement.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('AgentManagement.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ['AgentTrialDialog', 'AgentActivationDialog', 'EditAgentModal'];
let main;
function visit(node) {
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(tree) === 'main') main = node;
  ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(main, 'Use the actual sibling modal elements from the employee page');
const children = main.children.filter(node => names.some(name => {
  let found = false;
  function check(child) {
    if (ts.isJsxSelfClosingElement(child) && child.tagName.getText(tree) === name) found = true;
    ts.forEachChild(child, check);
  }
  check(node);
  return found;
}));
assert.equal(children.length, 3);
const code = ts.transpileModule(`exports.render = () => <>${children.map(node => node.getText(tree)).join('\n')}</>;`, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const employee = id => ({ agent: { id, name: 'Test employee' }, identity: 'project-a:staff-a' });

function render({ trialAgent = null, activationAgent = null, trialIdentity = 'project-a:staff-a' } = {}) {
  const changes = [];
  const scope = {
    exports: {}, require, trialAgent, activationAgent, trialIdentity,
    auditAgentId: null, selectedAgent: null, showEditAgent: false,
    setTrialAgent: value => changes.push(['trial', value]), setActivationAgent: () => {},
    setShowEditAgent: () => {}, setAuditAgentId: () => {}, setAuditRevision: () => {},
    ...Object.fromEntries(names.map(name => [name, name])),
  };
  vm.runInNewContext(code, scope);
  const elements = scope.exports.render().props.children.filter(Boolean);
  return { elements, changes };
}

for (const state of [
  { label: 'trial alongside mounted editor', trialAgent: employee('agent-a') },
  { label: 'trial and activation alongside editor', trialAgent: employee('agent-a'), activationAgent: employee('agent-a') },
]) {
  test(`every sibling dialog has a distinct key: ${state.label}`, () => {
    const { elements } = render(state);
    const keys = elements.map(element => element.key);
    assert.ok(keys.every(key => typeof key === 'string' && key.length > 0));
    assert.equal(new Set(keys).size, elements.length, 'Duplicate keys can leave a closed trial in the DOM');
  });
}

test('closing a trial clears the parent state without changing editor identity', () => {
  const open = render({ trialAgent: employee('agent-a') });
  open.elements.find(element => element.type === 'AgentTrialDialog').props.onClose();
  assert.deepEqual(open.changes, [['trial', null]]);
  const closed = render();
  assert.equal(closed.elements.some(element => element.type === 'AgentTrialDialog'), false);
  assert.equal(open.elements.find(element => element.type === 'EditAgentModal').key,
    closed.elements.find(element => element.type === 'EditAgentModal').key);
});

test('identity changes suppress the previous trial and remount the editor', () => {
  const previous = render({ trialAgent: employee('agent-a') });
  const switched = render({ trialAgent: employee('agent-a'), trialIdentity: 'project-b:staff-b' });
  assert.equal(switched.elements.some(element => element.type === 'AgentTrialDialog'), false);
  assert.notEqual(previous.elements.find(element => element.type === 'EditAgentModal').key,
    switched.elements.find(element => element.type === 'EditAgentModal').key);
});
