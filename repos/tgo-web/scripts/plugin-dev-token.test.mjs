import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/settings/PluginsSettings.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('PluginsSettings.tsx', source, ts.ScriptTarget.Latest, true);
let callback;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleGenerateToken') callback = node.initializer;
  ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(callback);

function handler(role = 'admin', request = async () => ({ token: 'fixture-token' })) {
  const calls = [];
  const tokens = [];
  const opened = [];
  const errors = [];
  const scope = {
    user: { role, project_id: 'fixture-project' },
    generatingTokenRef: { current: false },
    setIsGeneratingToken() {},
    generateDevToken: async project => { calls.push(project); return request(); },
    setDevToken: token => tokens.push(token),
    setShowTokenModal: show => opened.push(show),
    t: (_key, fallback) => fallback,
    alert: message => errors.push(message),
  };
  vm.runInNewContext(ts.transpileModule(`globalThis.submit = ${callback.getText(tree)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, scope);
  return { scope, calls, tokens, opened, errors, submit: () => scope.submit() };
}

for (const role of ['user', 'agent', undefined]) {
  test(`role ${role} cannot generate a plugin debug token`, async () => {
    const form = handler(role ?? '');
    await form.submit();
    assert.equal(form.calls.length, 0);
    assert.equal(form.opened.length, 0);
  });
}

test('rapid double click generates one token for the current project', async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const form = handler('admin', () => pending);
  const first = form.submit();
  const second = form.submit();
  assert.deepEqual(form.calls, ['fixture-project']);
  finish({ token: 'fixture-token' });
  await Promise.all([first, second]);
  assert.deepEqual(form.tokens, ['fixture-token']);
  assert.deepEqual(form.opened, [true]);
  assert.equal(form.scope.generatingTokenRef.current, false);
});

test('failed generation does not show a token and allows retry', async () => {
  let attempts = 0;
  const form = handler('admin', async () => {
    if (++attempts === 1) throw new Error('Forbidden');
    return { token: 'fixture-token' };
  });
  await form.submit();
  assert.equal(form.tokens.length, 0);
  assert.equal(form.opened.length, 0);
  assert.equal(form.errors.length, 1);
  assert.equal(form.scope.generatingTokenRef.current, false);
  await form.submit();
  assert.deepEqual(form.tokens, ['fixture-token']);
});
