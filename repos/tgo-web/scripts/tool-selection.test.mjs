import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/ai/ToolSelectionModal.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('ToolSelectionModal.tsx', source, ts.ScriptTarget.Latest, true);
let checkbox;
let categoryFilter;
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(tree) === 'filtered.filter') {
    categoryFilter = node.arguments[0];
  }
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(tree) === 'input' &&
      node.attributes.properties.some(prop => ts.isJsxAttribute(prop) && prop.name.text === 'type' && prop.initializer?.text === 'checkbox')) checkbox = node;
  ts.forEachChild(node, visit);
}

for (const [category, transport, expected] of [
  ['custom', 'http_webhook', true],
  ['custom', undefined, true],
  ['custom', null, true],
  ['custom', 'http', false],
  ['custom', 'sse', false],
  ['custom', 'plugin', false],
  ['tool_server', 'http', true],
  ['tool_server', 'sse', true],
  ['tool_server', 'http_webhook', false],
  ['plugin', 'plugin', true],
  ['device_control', 'device_control', true],
]) {
  test(`${category} category matches ${transport}: ${expected}`, () => {
    assert.ok(categoryFilter);
    const context = { selectedCategory: category };
    vm.runInNewContext(ts.transpileModule(`globalThis.filter = ${categoryFilter.getText(tree)}`, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText, context);
    assert.equal(context.filter({ config: { transport_type: transport } }), expected);
  });
}
visit(tree);
assert.ok(checkbox);

function handler(name, scope) {
  const attribute = checkbox.attributes.properties.find(prop => ts.isJsxAttribute(prop) && prop.name.text === name);
  if (!attribute?.initializer?.expression) return undefined;
  const context = { ...scope };
  vm.runInNewContext(ts.transpileModule(`globalThis.callback = ${attribute.initializer.expression.getText(tree)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  return context.callback;
}

for (const initial of [false, true]) {
  test(`checkbox toggles once with bubbling, initially ${initial}`, () => {
    let selected = initial;
    let toggles = 0;
    let stopped = false;
    const tool = { id: 'fixture' };
    const toggle = value => { assert.equal(value, tool); selected = !selected; toggles++; };
    const scope = { tool, handleToolClick: toggle };
    handler('onClick', scope)?.({ stopPropagation() { stopped = true; } });
    if (!stopped) toggle(tool); // The actual clickable tool row.
    handler('onChange', scope)();
    assert.equal(toggles, 1);
    assert.equal(selected, !initial);
  });
}
