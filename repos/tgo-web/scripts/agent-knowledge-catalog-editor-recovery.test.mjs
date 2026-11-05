import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/business/AgentKnowledgeCatalogEditor.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('AgentKnowledgeCatalogEditor.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let effect;
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect') {
    assert.equal(effect, undefined, 'Select the actual catalog loading effect');
    effect = node.arguments[0];
  }
  ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(effect);
const code = ts.transpileModule(`globalThis.effect = ${effect.getText(tree)};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

async function run({ recovered, unmount = false }) {
  let resolve;
  const reading = new Promise(done => { resolve = done; });
  let refreshes = 0;
  const scope = { agentId: 'agent-a', load: id => { assert.equal(id, 'agent-a'); return reading; },
    useAgentTrialReviewStore: { getState: () => ({ refresh: async () => { refreshes++; } }) } };
  vm.runInNewContext(code, scope);
  const cleanup = scope.effect();
  if (unmount) cleanup?.();
  resolve(recovered); await reading; await Promise.resolve();
  return refreshes;
}

test('recovering a confirmed catalog rereads question presets and report review state', async () => {
  assert.equal(await run({ recovered: true }), 1);
});
test('ordinary reads and unresolved writes do not trigger extra review refreshes', async () => {
  assert.equal(await run({ recovered: false }), 0);
});
test('an unmounted employee or identity effect cannot refresh a new review session', async () => {
  assert.equal(await run({ recovered: true, unmount: true }), 0);
});
