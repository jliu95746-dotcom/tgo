import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/ai/AgentManagement.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('AgentManagement.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let refresh;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleRefresh') refresh = node.initializer;
  ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(refresh, 'Test the real refresh button handler');
const code = ts.transpileModule(`globalThis.run = ${refresh.getText(tree)};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

async function run({ agentError = null, loading = false, modelStatus = 'loaded',
  modelProject = 'project-a', authProject = 'project-a', silent = false, reject = false } = {}) {
  const notices = [];
  const refreshing = [];
  const scope = {
    projectId: 'project-a',
    loadAgents: async () => { if (reject) throw new Error('fixture failure'); },
    loadModelCheck: async () => {},
    useAIStore: { getState: () => ({ agentsError: agentError, isLoadingAgents: loading }) },
    useAgentReadinessStore: { getState: () => ({ projectId: modelProject, status: modelStatus }) },
    useAuthStore: { getState: () => ({ user: { project_id: authProject } }) },
    setIsRefreshing: value => refreshing.push(value),
    showSuccess: (...args) => notices.push(['success', ...args]),
    showError: (...args) => notices.push(['error', ...args]),
    t: key => key,
  };
  vm.runInNewContext(code, scope);
  await scope.run(silent);
  assert.deepEqual(refreshing, [true, false]);
  return notices;
}

test('only a completed list and model check report success', async () => {
  assert.equal((await run())[0][0], 'success');
});

for (const options of [{ agentError: 'offline' }, { loading: true }, { reject: true }]) {
  test(`list refresh is not successful: ${JSON.stringify(options)}`, async () => {
    const notices = await run(options);
    assert.equal(notices[0][0], 'error');
    assert.equal(notices[0][1], 'agents.messages.refreshFailed');
  });
}

for (const options of [{ modelStatus: 'error' }, { modelStatus: 'loading' },
  { modelStatus: 'idle' }, { modelProject: 'project-b' }]) {
  test(`updated list with unverified models reports partial failure: ${JSON.stringify(options)}`, async () => {
    const notices = await run(options);
    assert.equal(notices[0][0], 'error');
    assert.equal(notices[0][1], 'agents.messages.refreshConfigurationFailed');
  });
}

test('project switch suppresses stale success and failure notifications', async () => {
  assert.deepEqual(await run({ authProject: 'project-b' }), []);
  assert.deepEqual(await run({ authProject: 'project-b', reject: true }), []);
});

test('silent refresh remains silent for success and recorded failures', async () => {
  assert.deepEqual(await run({ silent: true }), []);
  assert.deepEqual(await run({ silent: true, modelStatus: 'error' }), []);
  assert.deepEqual(await run({ silent: true, agentError: 'offline' }), []);
});

test('partial-failure notifications have translated title and explanation', () => {
  for (const language of ['zh', 'en']) {
    const locale = JSON.parse(readFileSync(new URL(`../src/i18n/locales/${language}.json`, import.meta.url), 'utf8'));
    assert.ok(locale.agents.messages.refreshConfigurationFailed);
    assert.ok(locale.agents.messages.refreshConfigurationFailedDesc);
  }
});
