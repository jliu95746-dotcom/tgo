import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createRequire } from 'node:module';

const requireModule = createRequire(import.meta.url);

function load(source, scope = {}) {
  const context = { exports: {}, console, atob, Date, ...scope };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    esModuleInterop: true,
  } }).outputText, context);
  return context.exports;
}

function storage() {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key) };
}

const token = expiration => `header.${Buffer.from(JSON.stringify({ exp: expiration })).toString('base64url')}.signature`;

test('access-token expiry triggers renewal before protected requests', () => {
  const { tokenNeedsRefresh } = load(readFileSync(
    new URL('../src/services/staffSession.ts', import.meta.url), 'utf8'));
  assert.equal(tokenNeedsRefresh(token(200), 100_000), false);
  assert.equal(tokenNeedsRefresh(token(130), 100_000), true);
  assert.equal(tokenNeedsRefresh(token(99), 100_000), true);
  assert.equal(tokenNeedsRefresh('malformed', 100_000), true);
});

test('only visible, trusted user activity renews the idle window', async () => {
  const { observeSessionActivity } = load(readFileSync(
    new URL('../src/services/staffSession.ts', import.meta.url), 'utf8'));
  const handlers = new Map();
  const document = { visibilityState: 'visible',
    addEventListener: (name, handler) => handlers.set(name, handler),
    removeEventListener: name => handlers.delete(name) };
  let now = 0;
  let touches = 0;
  const stop = observeSessionActivity(async () => { touches += 1; }, document, () => now);
  await handlers.get('pointerdown')({ isTrusted: true });
  assert.equal(touches, 1);
  now = 61_000;
  document.visibilityState = 'hidden';
  await handlers.get('keydown')({ isTrusted: true });
  assert.equal(touches, 1);
  document.visibilityState = 'visible';
  await handlers.get('pointerdown')({ isTrusted: false });
  assert.equal(touches, 1);
  await handlers.get('visibilitychange')({ isTrusted: true });
  assert.equal(touches, 2);
  stop();
  assert.equal(handlers.size, 0);
});

test('all protected transport modes prepare the company session; operator calls stay independent', async () => {
  const requests = [];
  const localStorage = storage();
  localStorage.setItem('tgo-auth-token', 'expired-company-token');
  const api = load(readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8')
    .replaceAll('import.meta.env', 'testEnv'), {
    localStorage, sessionStorage: storage(), testEnv: {}, URLSearchParams,
    require: () => ({ language: 'zh' }),
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      return { ok: true, status: 204, json: async () => ({}) };
    },
  });
  let preparations = 0;
  api.setSessionRequestHandler(async () => {
    preparations += 1;
    api.apiClient.setToken('renewed-company-token');
  });
  await api.apiClient.get('/v1/staff');
  await api.apiClient.getResponse('/v1/files');
  await api.apiClient.postFormData('/v1/files', {});
  // Streaming uses the same preparation even when there is no response body.
  await assert.rejects(api.apiClient.stream('/v1/chat', {}, { onMessage() {} }));
  assert.equal(preparations, 4);
  for (const request of requests) {
    assert.equal(request.headers.Authorization, 'Bearer renewed-company-token');
    assert.equal(request.credentials, 'include');
  }
  const ops = api.createScopedApiClient('operator', () => {});
  ops.setToken('operator-token');
  await ops.get('/v1/ops/me');
  await api.apiClient.post('/v1/staff/session/refresh?active=true');
  assert.equal(preparations, 4);
});

function loadStore() {
  const localStorage = storage();
  const user = { id: 'member', project_id: 'company', username: 'fixture@example.com', role: 'admin' };
  localStorage.setItem('auth-state', JSON.stringify({ version: 0, state: {
    user, token: token(200), isAuthenticated: true,
  } }));
  let clientToken;
  const pending = [];
  let prepare;
  const api = { apiClient: { setToken: value => { clientToken = value; } },
    authAPI: { refreshSession: active => new Promise(resolve => pending.push({ active, resolve })),
      logout: async () => { clientToken = null; } },
    setSessionRequestHandler: handler => { prepare = handler; } };
  const previousStorage = globalThis.localStorage;
  globalThis.localStorage = localStorage;
  let exports;
  try {
    exports = load(readFileSync(new URL('../src/stores/authStore.ts', import.meta.url), 'utf8'), {
      localStorage, sessionStorage: { clear() {} }, window: { location: {} },
      console: { log() {}, warn() {}, error() {} },
      require: name => {
        if (name.startsWith('zustand')) return requireModule(name);
        if (name === '@/constants') return { STORAGE_KEYS: { AUTH: 'auth-state' } };
        if (name === '@/services/api') return api;
        if (name === '@/services/staffSession') return { tokenNeedsRefresh: () => true };
        if (name === '@/services/wukongimWebSocket') return { wukongimWebSocketService: { safeDisconnect() {} } };
        if (name === './chatStore') return { useChatStore: { getState: () => ({ clearStore() {} }) } };
        throw new Error(name);
      },
    });
  } finally {
    globalThis.localStorage = previousStorage;
  }
  return { store: exports.useAuthStore, pending, localStorage, user,
    clientToken: () => clientToken, prepare: () => prepare() };
}

test('reopening the browser restores persisted auth and parallel requests share one refresh', async () => {
  const app = loadStore();
  assert.equal(app.store.getState().isAuthenticated, true);
  assert.equal(app.clientToken(), token(200));
  const first = app.prepare();
  const second = app.prepare();
  assert.equal(app.pending.length, 1);
  app.pending[0].resolve({ access_token: token(300), staff: app.user });
  await Promise.all([first, second]);
  assert.equal(app.clientToken(), token(300));
  assert.equal(JSON.parse(app.localStorage.getItem('auth-state')).state.token, token(300));
});

test('human activity arriving during background renewal is recorded after it completes', async () => {
  const app = loadStore();
  const background = app.store.getState().refreshSession(false);
  const active = app.store.getState().refreshSession(true);
  app.pending[0].resolve({ access_token: token(300), staff: app.user });
  await background;
  await Promise.resolve();
  assert.equal(app.pending.length, 2);
  assert.equal(app.pending[1].active, true);
  app.pending[1].resolve({ access_token: token(300), staff: app.user });
  await active;
});

test('late renewal never resurrects a logged out account', async () => {
  const app = loadStore();
  const renewal = app.store.getState().refreshSession(true);
  await app.store.getState().logout();
  app.pending[0].resolve({ access_token: token(300), staff: app.user });
  await renewal;
  assert.equal(app.store.getState().isAuthenticated, false);
  assert.equal(app.store.getState().token, null);
  assert.equal(app.clientToken(), null);
});

test('background timer does not mark activity and logout propagates to other tabs', async () => {
  const listeners = new Map();
  const renewals = [];
  const logouts = [];
  let timer;
  let cleanup;
  const state = { isAuthenticated: true, isSessionReady: true, token: 'access',
    refreshSession: async active => { renewals.push(active); },
    setSessionReady() {}, logout: async revoke => { logouts.push(revoke); } };
  const useAuthStore = Object.assign(selector => selector(state), { getState: () => state });
  const hook = load(readFileSync(new URL('../src/hooks/useStaffSession.ts', import.meta.url), 'utf8'), {
    document: { visibilityState: 'visible' },
    window: { addEventListener: (name, handler) => listeners.set(name, handler),
      removeEventListener: name => listeners.delete(name),
      setInterval: callback => { timer = callback; return 1; }, clearInterval() {} },
    require: name => {
      if (name === 'react') return { useEffect: callback => { cleanup = callback(); } };
      if (name === '@/stores/authStore') return { useAuthStore };
      if (name === '@/services/staffSession') return { tokenNeedsRefresh: () => true, observeSessionActivity: () => () => {} };
      if (name === '@/constants') return { STORAGE_KEYS: { AUTH: 'auth-state', AUTH_TOKEN: 'tgo-auth-token' } };
      throw new Error(name);
    },
  });
  assert.equal(hook.useStaffSession(), true);
  timer();
  assert.deepEqual(renewals, [true, false]);
  listeners.get('storage')({ key: 'tgo-auth-token', newValue: null });
  assert.deepEqual(logouts, [false]);
  cleanup();
  assert.equal(listeners.size, 0);
});
