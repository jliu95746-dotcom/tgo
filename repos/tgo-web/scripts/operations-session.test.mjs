import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

function loadClient() {
  const source = readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8')
    .replaceAll('import.meta.env', 'testEnv');
  const localStorage = storage();
  const sessionStorage = storage();
  localStorage.setItem('tgo-auth-token', 'tenant-session');
  const requests = [];
  let status = 200;
  const scope = { exports: {}, localStorage, sessionStorage, testEnv: {}, console,
    require: () => ({ language: 'zh' }),
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      return { ok: status === 200, status, json: async () => ({ error: { message: 'unauthorized' } }) };
    },
  };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, scope);
  return { ...scope.exports, localStorage, sessionStorage, requests,
    respond: value => { status = value; } };
}

test('operator login uses session storage and never overwrites the company session', async () => {
  const app = loadClient();
  const ops = app.createScopedApiClient('yujian-operations-token', () => {});
  assert.equal(ops.getToken(), null);
  ops.setToken('operator-session');
  await ops.get('/v1/ops/me');
  await app.apiClient.get('/v1/staff/me');
  assert.equal(app.requests[0].headers.Authorization, 'Bearer operator-session');
  assert.equal(app.requests[1].headers.Authorization, 'Bearer tenant-session');
  assert.equal(app.localStorage.getItem('tgo-auth-token'), 'tenant-session');
  assert.equal(app.sessionStorage.getItem('yujian-operations-token'), 'operator-session');
  ops.setToken(null);
  assert.equal(app.apiClient.getToken(), 'tenant-session');
});

test('expired operator sessions do not invoke company logout', async () => {
  const app = loadClient();
  let tenantLogouts = 0;
  let operatorLogouts = 0;
  app.setUnauthorizedHandler(() => { tenantLogouts += 1; });
  const ops = app.createScopedApiClient('yujian-operations-token', () => { operatorLogouts += 1; });
  ops.setToken('expired-operator');
  app.respond(401);
  await assert.rejects(ops.get('/v1/ops/me'));
  assert.equal(operatorLogouts, 1);
  assert.equal(tenantLogouts, 0);
  await assert.rejects(app.apiClient.get('/v1/staff/me'));
  assert.equal(tenantLogouts, 1);
  assert.equal(operatorLogouts, 1);
});
