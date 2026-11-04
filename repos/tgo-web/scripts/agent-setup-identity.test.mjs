import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const subscribers = [];
  const auth = { getState: () => ({ user }), subscribe: callback => subscribers.push(callback) };
  let finish;
  const api = { createAgent: () => new Promise(resolve => { finish = resolve; }), updateAgent: () => new Promise(resolve => { finish = resolve; }), getAgents: () => new Promise(resolve => { finish = resolve; }) };
  const create = () => initializer => {
    let state;
    const set = value => { state = { ...state, ...(typeof value === 'function' ? value(state) : value) }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/stores/aiStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, { exports, console: { error() {} }, require: name => {
    if (name === 'zustand') return { create };
    if (name === 'zustand/middleware') return { devtools: initializer => initializer, persist: initializer => initializer };
    if (name === './authStore') return { useAuthStore: auth };
    if (name === './onboardingStore') return { useOnboardingStore: { getState: () => ({ markTaskCompleted() {} }) } };
    if (name === './agentActivationStore') return { useAgentActivationStore: { getState: () => ({ invalidate() {} }) } };
    if (name === '@/i18n') return { __esModule: true, default: { t: key => key } };
    if (name === '@/services/aiAgentsApi') return { AIAgentsApiService: api, AIAgentsTransformUtils: { transformFormDataToCreateRequest: data => data, transformApiAgentToAgent: data => data,
      transformAgentPatch: (_agent, updates) => updates.status ? { is_active: updates.status === 'active' } : updates } };
    throw Error(`Unexpected dependency ${name}`);
  } });
  return { store: exports.useAIStore,
    changeUser: next => { const previous = { user }; user = next; subscribers.forEach(callback => callback({ user }, previous)); },
    finish: data => finish(data),
  };
}

test('a late edit response does not replace another staff member in the same project', async () => {
  const h = fixture(); h.store.setState({ agents: [{ id: 'agent-a', name: '原客服' }] });
  const request = h.store.getState().updateAgent('agent-a', { name: '旧请求名称' });
  h.changeUser({ id: 'staff-b', project_id: 'project-a', role: 'admin' });
  h.store.setState({ agents: [{ id: 'agent-a', name: '当前管理员的新修改' }] });
  h.finish({ id: 'agent-a', name: '旧请求名称' }); await request;
  assert.equal(h.store.getState().agents.length, 1);
  assert.equal(h.store.getState().agents[0].name, '当前管理员的新修改');
});

test('an old roster response cannot replace a new identity roster or loading status', async () => {
  const h = fixture(), request = h.store.getState().loadAgents();
  h.changeUser({ id: 'staff-b', project_id: 'project-a', role: 'admin' });
  h.store.setState({ agents: [{ id: 'agent-a', name: '当前名单' }], isLoadingAgents: true });
  h.finish({ data: [{ id: 'agent-a', name: '旧名单' }] }); await request;
  assert.equal(h.store.getState().agents[0].name, '当前名单');
  assert.equal(h.store.getState().isLoadingAgents, true);
});

test('role revocation clears configuration and discards a pending edit', async () => {
  const h = fixture(); h.store.setState({ agents: [{ id: 'agent-a', name: '原客服' }], showCreateAgentModal: true });
  const request = h.store.getState().updateAgent('agent-a', { name: '旧请求名称' });
  h.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  h.finish({ id: 'agent-a', name: '旧请求名称' }); await request;
  assert.equal(h.store.getState().agents.length, 0);
  assert.equal(h.store.getState().showCreateAgentModal, false);
});

test('ordinary staff cannot dispatch an employee configuration mutation', async () => {
  const h = fixture(); h.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  h.store.setState({ agents: [{ id: 'agent-a' }] });
  await assert.rejects(h.store.getState().updateAgent('agent-a', { name: '不允许' }), /forbidden/);
});

test('an old activation finally block cannot clear a new identity pending action', async () => {
  const h = fixture(); h.store.setState({ agents: [{ id: 'agent-a' }] });
  const request = h.store.getState().updateAgent('agent-a', { status: 'active' });
  h.changeUser({ id: 'staff-b', project_id: 'project-a', role: 'admin' });
  h.store.setState({ isChangingAgentActivation: true });
  h.finish({ id: 'agent-a' }); await request;
  assert.equal(h.store.getState().isChangingAgentActivation, true);
});

test('changing staff identity clears the open business setup, including company and resources', () => {
  const h = fixture();
  h.store.getState().setShowCreateAgentModal(true);
  h.store.getState().setCreateAgentFormData({ name: '前企业客服', companySummary: '前企业资料', scenarioId: 'knowledge', knowledgeBases: ['private-kb'] });
  h.changeUser({ id: 'staff-b', project_id: 'project-a' });
  assert.equal(h.store.getState().showCreateAgentModal, false);
  assert.equal(h.store.getState().createAgentFormData.name, '');
  assert.equal(h.store.getState().createAgentFormData.companySummary, undefined);
  assert.equal(h.store.getState().createAgentFormData.knowledgeBases.length, 0);
});

test('an in-flight creation from the previous identity never updates the new session', async () => {
  const h = fixture();
  const request = h.store.getState().createAgent({ name: '旧企业员工' });
  h.changeUser({ id: 'staff-b', project_id: 'project-b' });
  h.finish({ id: 'old-agent', name: '旧企业员工', project_id: 'project-a' });
  await assert.rejects(request);
  assert.equal(h.store.getState().agents.length, 0);
  assert.equal(h.store.getState().agentsError, null);
  assert.equal(h.store.getState().isCreatingAgent, false);
});
