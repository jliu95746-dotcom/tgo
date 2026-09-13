import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const transpile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

function deleteDialog(overrides = {}) {
  const source = readFileSync(new URL('../src/components/ai/Tools.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('Tools.tsx', source, ts.ScriptTarget.Latest, true);
  let handler;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleDeleteToolConfirm') handler = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(handler);
  const events = [];
  const scope = {
    toolToDelete: { id: 'tool-a', name: '查询工具' }, deletingRef: { current: false },
    setIsDeleting: value => events.push(['busy', value]),
    setShowDeleteConfirm: value => events.push(['dialog', value]),
    setToolToDelete: value => events.push(['target', value]),
    deleteTool: async id => events.push(['delete', id]),
    loadTools: async value => events.push(['refresh', value]),
    showToast: (...args) => events.push(['toast', ...args]),
    t: (_key, fallback) => fallback, Error, console: { error() {} }, ...overrides,
  };
  vm.runInNewContext(transpile(`globalThis.confirm = ${handler.getText(tree)}`), scope);
  return { scope, events, confirm: () => scope.confirm() };
}

function toolStore(api) {
  const source = readFileSync(new URL('../src/stores/projectToolsStore.ts', import.meta.url), 'utf8');
  const scope = { exports: {}, Error, console: { error() {} }, require: name => {
    if (name === '@/services/projectToolsApi') return { ProjectToolsApiService: api };
    return require(name);
  } };
  vm.runInNewContext(transpile(source), scope);
  return scope.exports.useProjectToolsStore;
}

test('delete confirmation submits once even before React can disable the button', async () => {
  const pending = deferred();
  let requests = 0;
  const dialog = deleteDialog({ deleteTool: () => { requests++; return pending.promise; } });
  const first = dialog.confirm();
  const second = dialog.confirm();
  const countBeforeResponse = requests;
  pending.resolve();
  await Promise.all([first, second]);
  assert.equal(countBeforeResponse, 1);
  assert.equal(dialog.events.filter(event => event[0] === 'refresh').length, 1);
  assert.equal(dialog.events.filter(event => event[0] === 'dialog').length, 1);
  assert.equal(dialog.scope.deletingRef.current, false);
});

test('failed deletion retains the target and dialog, shows the error and allows retry', async () => {
  let requests = 0;
  const dialog = deleteDialog({ deleteTool: async () => {
    if (++requests === 1) throw new Error('HTTP 503');
  } });
  await dialog.confirm();
  assert.equal(dialog.events.some(event => ['target', 'dialog', 'refresh'].includes(event[0])), false);
  assert.equal(dialog.events.find(event => event[0] === 'toast')[3], 'HTTP 503');
  assert.equal(dialog.scope.deletingRef.current, false);
  await dialog.confirm();
  assert.equal(requests, 2);
  assert.ok(dialog.events.some(event => event[0] === 'target' && event[1] === null));
});

test('delete without a selected tool is a no-op', async () => {
  const dialog = deleteDialog({ toolToDelete: null });
  await dialog.confirm();
  assert.deepEqual(dialog.events, []);
});

test('parallel edits to different tools preserve both saved responses', async () => {
  const first = deferred();
  const second = deferred();
  const store = toolStore({ updateAiTool: id => id === 'a' ? first.promise : second.promise });
  store.getState().setAiTools([{ id: 'a', name: 'old-a' }, { id: 'b', name: 'old-b' }]);
  const editA = store.getState().updateTool('a', { name: 'new-a' });
  const editB = store.getState().updateTool('b', { name: 'new-b' });
  second.resolve({ id: 'b', name: 'new-b' });
  await editB;
  first.resolve({ id: 'a', name: 'new-a' });
  await editA;
  assert.deepEqual(Array.from(store.getState().aiTools, tool => tool.name), ['new-a', 'new-b']);
});

test('deletion finishing after another edit does not restore stale tool details', async () => {
  const pending = deferred();
  const store = toolStore({ deleteAiTool: () => pending.promise,
    updateAiTool: async id => ({ id, name: 'new-b' }) });
  store.getState().setAiTools([{ id: 'a' }, { id: 'b', name: 'old-b' }]);
  const deletion = store.getState().deleteTool('a');
  await store.getState().updateTool('b', { name: 'new-b' });
  pending.resolve({ id: 'a', deleted_at: '2026-09-11T00:00:00Z' });
  await deletion;
  assert.equal(store.getState().aiTools.find(tool => tool.id === 'b').name, 'new-b');
  assert.ok(store.getState().aiTools.find(tool => tool.id === 'a').deleted_at);
});

test('an edit response preserves newly loaded tools and does not resurrect removed items', async () => {
  const pending = deferred();
  const store = toolStore({ updateAiTool: () => pending.promise });
  store.getState().setAiTools([{ id: 'a' }, { id: 'removed' }]);
  const edit = store.getState().updateTool('a', { name: 'saved' });
  store.getState().setAiTools([{ id: 'a' }, { id: 'new' }]);
  pending.resolve({ id: 'a', name: 'saved' });
  await edit;
  assert.deepEqual(Array.from(store.getState().aiTools, tool => tool.id), ['a', 'new']);
});

test('failed mutations leave tool data unchanged and propagate the error', async () => {
  const error = new Error('HTTP 503');
  const store = toolStore({ updateAiTool: async () => { throw error; },
    deleteAiTool: async () => { throw error; } });
  const tools = [{ id: 'a', name: 'unchanged' }];
  store.getState().setAiTools(tools);
  await assert.rejects(store.getState().updateTool('a', {}), error);
  await assert.rejects(store.getState().deleteTool('a'), error);
  assert.equal(store.getState().aiTools, tools);
  assert.equal(store.getState().isDeleting, false);
});
