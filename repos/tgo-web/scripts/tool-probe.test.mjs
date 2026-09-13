import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import vm from 'node:vm';

const read = file => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
function handler(file, name, scope) {
  const source = read(file);
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  let expression;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === name) expression = node.initializer.getText(tree);
    ts.forEachChild(node, visit);
  }
  visit(tree);
  vm.runInNewContext(ts.transpileModule(`globalThis.run = ${expression}`, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText, scope);
  return scope.run;
}

test('saved tool test requires confirmation and valid object input', async () => {
  let calls = 0;
  const results = [];
  const scope = { confirmed: false, input: '{}', pending: {current: false}, active: {current: true}, tool: {id:'owned'}, JSON, Array, Error,
    t: key => key, setBusy() {}, setResult: value => results.push(value), toolProbeApi: { execute: async () => { calls++; return {success:true}; } } };
  const run = handler('components/ai/SavedToolTestModal.tsx', 'run', scope);
  await run(); assert.equal(calls, 0);
  scope.confirmed = true; scope.input = '[]'; await run(); assert.equal(calls, 0);
  assert.equal(results.at(-1).success, false);
  scope.input = '{"code":"fixture"}'; await run(); assert.equal(calls, 1);
});

test('discovery does not apply a response after the connection component is replaced', async () => {
  let finish;
  const promise = new Promise(resolve => { finish = resolve; });
  const applied = [];
  const scope = {pending: {current:false}, active: {current:true}, connection:{}, setBusy(){}, setMessage(){}, setTools: value=>applied.push(value), t: key=>key, toolProbeApi:{discover:()=>promise}};
  const run = handler('components/ai/MCPDiscoveryField.tsx', 'discover', scope);
  const pending = run(); scope.active.current = false; finish({success:true,tools:[{name:'stale'}]}); await pending;
  assert.equal(applied.length, 1); assert.equal(applied[0].length, 0);
  assert.match(read('components/ai/AddToolModal.tsx'), /key=\{JSON.stringify\(\[formData.endpoint, formData.transport_type, formData.headers\]\)\}/);
});

test('tool store entry and dedicated pages are gone, both add buttons retain visible labels', () => {
  const page = read('components/ai/Tools.tsx');
  assert.doesNotMatch(page, /ToolStoreModal|hidden lg:inline/);
  assert.match(page, /SavedToolTestModal/);
  for (const name of ['ToolStoreModal','ToolStoreCard','ToolStoreDetail','ToolStoreCategoryFilter']) assert.equal(existsSync(new URL(`../src/components/ai/${name}.tsx`, import.meta.url)), false);
  for (const language of ['zh','en']) assert.ok(JSON.parse(read(`i18n/locales/${language}.json`)).tools.probe.connect);
});
