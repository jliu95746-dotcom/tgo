import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function parse(file) {
  return ts.createSourceFile(file, readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true);
}
function find(tree, predicate) {
  let result;
  function visit(node) {
    if (!result && predicate(node)) result = node;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(result, 'expected actual component code');
  return result;
}
function evaluate(expression, scope) {
  const compiled = ts.transpileModule(`globalThis.result = ${expression};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const context = { console, ...scope };
  vm.runInNewContext(compiled, context);
  return context.result;
}
function effect(file, marker) {
  const tree = parse(file);
  const call = find(tree, node => ts.isCallExpression(node) && /^(React\.)?useEffect$/.test(node.expression.getText(tree))
    && node.arguments[0].getText(tree).includes(marker));
  let dependencies;
  let cleanup;
  return {
    render(scope) {
      const next = evaluate(call.arguments[1].getText(tree), scope);
      if (dependencies && next.every((value, index) => Object.is(value, dependencies[index]))) return;
      cleanup?.();
      dependencies = next;
      cleanup = evaluate(call.arguments[0].getText(tree), scope)();
    },
    close() { cleanup?.(); },
  };
}
function value(file, name, scope) {
  const tree = parse(file);
  const declaration = find(tree, node => ts.isVariableDeclaration(node) && node.name.getText(tree) === name);
  const initializer = declaration.initializer;
  return evaluate(ts.isCallExpression(initializer) ? initializer.arguments[0].getText(tree) : initializer.getText(tree), scope);
}
function memo(file, name) {
  const tree = parse(file);
  const call = find(tree, node => ts.isVariableDeclaration(node) && node.name.getText(tree) === name).initializer;
  let dependencies;
  let result;
  return scope => {
    const next = evaluate(call.arguments[1].getText(tree), scope);
    if (!dependencies || next.some((item, index) => !Object.is(item, dependencies[index]))) {
      result = evaluate(call.arguments[0].getText(tree), scope)();
      dependencies = next;
    }
    return result;
  };
}
const flush = async () => { for (let index = 0; index < 8; index++) await Promise.resolve(); };
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const t = (_key, fallback) => fallback;
const i18n = { t };

test('history reloads when channel type changes and accepts channel type zero', () => {
  const calls = [];
  const run = effect('hooks/useHistoricalMessages.ts', 'loadHistoricalMessagesAction(');
  const scope = {
    channelId: 'same-id', channelType: 251, historicalMessages: [], isLoadingHistory: false,
    initialHistoryRequestKey: { current: null }, getChannelKey: (id, type) => `${type}:${id}`,
    loadHistoricalMessagesAction: (...args) => { calls.push(args); return Promise.resolve(); },
  };
  run.render(scope);
  run.render({ ...scope, channelType: 1 });
  run.render({ ...scope, channelType: 0 });
  assert.deepEqual(calls.map(args => args[1]), [251, 1, 0]);
});

test('history waits for an ongoing request but never loops on empty or failed history', () => {
  let requests = 0;
  const run = effect('hooks/useHistoricalMessages.ts', 'loadHistoricalMessagesAction(');
  const scope = {
    channelId: 'new-channel', channelType: 251, historicalMessages: [], isLoadingHistory: true,
    initialHistoryRequestKey: { current: null }, getChannelKey: (id, type) => `${type}:${id}`,
    loadHistoricalMessagesAction: () => { requests++; return Promise.resolve(); },
  };
  run.render(scope);
  assert.equal(requests, 0);
  run.render({ ...scope, isLoadingHistory: false });
  assert.equal(requests, 1);
  run.render(scope);
  run.render({ ...scope, isLoadingHistory: false });
  assert.equal(requests, 1);
});

test('knowledge list refresh and language changes do not erase an edited employee form', () => {
  let resets = 0;
  const run = effect('components/ai/EditAgentModal.tsx', 'reset({');
  const scope = {
    agent: { id: 'owned-agent', name: 'Original' }, isLoadingAgent: false,
    initializedAgent: { current: null }, knowledgeBases: [], reset: () => { resets++; }, t, i18n,
  };
  run.render(scope);
  assert.equal(resets, 1);
  run.render({ ...scope, knowledgeBases: [{ id: 'new-kb' }], t: () => 'translated' });
  assert.equal(resets, 1);
  run.render({ ...scope, agent: { id: 'replacement', name: 'Other' } });
  assert.equal(resets, 2);
});

test('an older employee fetch cannot replace the latest selected employee', async () => {
  const first = deferred();
  const second = deferred();
  const received = [];
  const run = value('components/ai/EditAgentModal.tsx', 'fetchAgent', {
    agentRequestVersion: { current: 0 }, setIsLoadingAgent() {}, setAgentError() {},
    setAgent: agent => { if (agent) received.push(agent.id); }, showToast() {}, t, i18n,
    AIAgentsApiService: { getAgent: id => id === 'first' ? first.promise : second.promise },
    AIAgentsTransformUtils: { transformApiAgentToAgent: response => response },
  });
  const older = run('first');
  const latest = run('second');
  second.resolve({ id: 'second' });
  await latest;
  first.resolve({ id: 'first' });
  await older;
  assert.deepEqual(received, ['second']);
});

test('drag-and-drop uses the current upload permission and destination callback', () => {
  const file = 'components/knowledge/FileUpload.tsx';
  const tree = parse(file);
  const declaration = find(tree, node => ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleDrop');
  const initializer = declaration.initializer;
  let previousDeps;
  let handler;
  const calls = [];
  for (const disabled of [true, false, true]) {
    const handleFiles = value(file, 'handleFiles', { isUploadDisabled: disabled, onUpload: () => calls.push(disabled), t });
    const scope = { handleFiles, setIsDragOver() {} };
    if (ts.isCallExpression(initializer)) {
      const deps = evaluate(initializer.arguments[1].getText(tree), scope);
      if (!previousDeps || deps.some((dep, index) => !Object.is(dep, previousDeps[index]))) {
        handler = evaluate(initializer.arguments[0].getText(tree), scope);
      }
      previousDeps = deps;
    } else {
      handler = evaluate(initializer.getText(tree), scope);
    }
    handler({ preventDefault() {}, stopPropagation() {}, dataTransfer: { files: [{ name: 'test.txt', size: 1 }] } });
  }
  assert.deepEqual(calls, [false]);
});

test('changing language does not create a second workflow draft', async () => {
  let created = 0;
  const run = effect('components/workflow/modals/WorkflowEditorModal.tsx', 'createWorkflow()');
  const scope = { isOpen: true, workflowId: null, loadWorkflow() {}, resetEditor() {},
    createWorkflow: async () => { created++; }, showToast() {}, t, i18n };
  run.render(scope);
  await flush();
  run.render({ ...scope, t: () => 'translated' });
  assert.equal(created, 1);
});

test('streaming equivalent form state preserves user edits and changed defaults replace it', () => {
  const render = memo('components/chat/jsonRender/JSONRenderSurface.tsx', 'store');
  const createStateStore = initial => ({ ...initial });
  const scope = { spec: { state: { choice: '' } }, stateKey: '{"choice":""}', createStateStore };
  const first = render(scope);
  first.choice = 'user edit';
  assert.equal(render({ ...scope, spec: { state: { choice: '' } } }), first);
  assert.equal(first.choice, 'user edit');
  const replaced = render({ ...scope, spec: { state: { choice: 'server' } }, stateKey: '{"choice":"server"}' });
  assert.notEqual(replaced, first);
  assert.equal(replaced.choice, 'server');
});

test('plugin context is stable on parent refresh and updates when the selected channel changes', () => {
  const render = memo('components/plugin/PluginPanelSection.tsx', 'memoizedContext');
  const context = { visitor_id: 'visitor', channel_id: 'first', channel_type: 251, platform_type: 'website', extension_type: 'visitor_panel' };
  const first = render({ context });
  assert.equal(render({ context: { ...context } }), first);
  const second = render({ context: { ...context, channel_id: 'second' } });
  assert.notEqual(second, first);
  assert.equal(second.channel_id, 'second');
});

test('changing provider discards a late model-list response', async () => {
  const file = 'components/settings/AddModelModal.tsx';
  const first = deferred();
  const second = deferred();
  const received = [];
  const run = effect(file, 'setRemoteModels([])');
  const scope = { isOpen: true, provider: { id: 'first' }, modelType: 'chat', isVision: false, isStore: false,
    setRemoteModels: models => received.push(models), setLoading() {}, setModelId() {}, setModelType() {}, setIsVision() {}, setSelectedModels() {},
    toast: null, t, getErrorMessage: error => error.message,
    AIProvidersApiService: class { getRemoteModels(id) { return id === 'first' ? first.promise : second.promise; } },
  };
  const firstScope = { ...scope, fetchAvailableModels: () => value(file, 'fetchAvailableModels', scope)() };
  run.render(firstScope);
  const nextScope = { ...scope, provider: { id: 'second' } };
  run.render({ ...nextScope, fetchAvailableModels: () => value(file, 'fetchAvailableModels', nextScope)() });
  second.resolve({ models: [{ id: 'new-model' }] });
  await flush();
  first.resolve({ models: [{ id: 'old-model' }] });
  await flush();
  assert.deepEqual(received.map(models => models[0]?.id).filter(Boolean), ['new-model']);
  run.close();
});

for (const resolveFirstBeforeSwitch of [true, false]) {
  test(`project model defaults refresh and reject stale data (first completes before switch: ${resolveFirstBeforeSwitch})`, async () => {
    const first = deferred();
    const second = deferred();
    const received = [];
    const run = effect('components/settings/ModelProvidersSettings.tsx', 'getAIConfig(');
    const scope = { projectId: 'first', isInitialized: { current: null }, activeProjectId: { current: 'first' },
      MODEL_TYPES: ['chat'], ensureFetchModelOptions: async () => {},
      ProjectConfigApiService: class { getAIConfig(id) { return id === 'first' ? first.promise : second.promise; } },
      selectedModelValue: (_provider, model) => model ?? '',
      setModelSelections: values => received.push(values.chat), setDefaultLlmModel() {}, setDefaultEmbeddingModel() {},
      toast: null, t, getErrorMessage: error => error.message,
    };
    run.render(scope);
    if (resolveFirstBeforeSwitch) { first.resolve({ default_chat_model: 'old-project-model' }); await flush(); }
    scope.activeProjectId.current = 'second';
    run.render({ ...scope, projectId: 'second' });
    second.resolve({ default_chat_model: 'new-project-model' });
    await flush();
    if (!resolveFirstBeforeSwitch) { first.resolve({ default_chat_model: 'old-project-model' }); await flush(); }
    assert.equal(received.at(-1), 'new-project-model');
    if (!resolveFirstBeforeSwitch) assert.deepEqual(received, ['new-project-model']);
    run.close();
  });
}
