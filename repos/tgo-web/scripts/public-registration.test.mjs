import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

function compile(code, scope) {
  const result = ts.transpileModule(code, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  });
  vm.runInNewContext(result.outputText, scope);
}

function loadSource(relative) {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8');
  return [source, ts.createSourceFile(relative, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)];
}

function findNode(tree, predicate) {
  let found;
  function visit(node) {
    if (predicate(node)) found = node;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(found);
  return found;
}

const [, apiTree] = loadSource('../src/services/api.ts');
const registerMethod = findNode(apiTree, node => ts.isMethodDeclaration(node) && node.name.getText(apiTree) === 'register');
const registrationError = findNode(apiTree, node => ts.isClassDeclaration(node) && node.name.text === 'RegistrationLoginError');

test('actual registration service uses the public endpoint without role or tenant overrides', async () => {
  const calls = [];
  const scope = { apiClient: { post: async (...args) => { calls.push(args); return { project_id: 'new-project' }; } } };
  compile(`globalThis.register = ({ ${registerMethod.getText(apiTree)} }).register;`, scope);
  const payload = { username: 'owner@example.com', password: 'test-password', project_name: '独立项目' };
  const result = await scope.register(payload);
  assert.equal(result.project_id, 'new-project');
  assert.equal(calls[0][0], '/v1/staff/register');
  assert.equal(calls[0][1], payload);
  assert.equal(calls[0][1].role, undefined);
  assert.equal(calls[0][1].project_id, undefined);
});

const [, storeTree] = loadSource('../src/stores/authStore.ts');
const registerAction = findNode(storeTree, node => ts.isPropertyAssignment(node) && node.name.getText(storeTree) === 'register');

function authFixture(loginFails = false) {
  const state = {};
  const requests = [];
  const scope = {
    exports: {}, console: { log() {} }, APIError: class extends Error {},
    set: data => Object.assign(state, data),
    authAPI: {
      register: async data => { requests.push(['register', data]); return { username: 'owner@example.com' }; },
      login: async data => {
        requests.push(['login', data]);
        if (loginFails) throw new Error('Temporary login outage');
        return { access_token: 'fixture-token', staff: {
          id: 'owner', username: 'owner@example.com', project_id: 'new-project', role: 'admin',
        } };
      },
    },
  };
  compile(registrationError.getText(apiTree), scope);
  scope.RegistrationLoginError = scope.exports.RegistrationLoginError;
  compile(`globalThis.register = ${registerAction.initializer.getText(storeTree)};`, scope);
  return { state, requests, register: scope.register };
}

test('actual auth store logs into the normalized new account and retains its project identity', async () => {
  const f = authFixture();
  await f.register({ email: 'OWNER@example.com', password: 'test-password', workspaceName: ' 新项目 ' });
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[0][1].project_name, '新项目');
  assert.equal(f.requests[1][1].username, 'owner@example.com');
  assert.equal(f.state.user.project_id, 'new-project');
  assert.equal(f.state.user.role, 'admin');
  assert.equal(f.state.isAuthenticated, true);
});

test('an automatic login failure is distinguished from account creation failure', async () => {
  const f = authFixture(true);
  await assert.rejects(f.register({ email: 'owner@example.com', password: 'test-password', workspaceName: '项目' }), {
    name: 'RegistrationLoginError',
  });
  assert.equal(f.requests.filter(([method]) => method === 'register').length, 1);
  assert.equal(f.state.isLoading, false);
  assert.notEqual(f.state.isAuthenticated, true);
});

test('the actual Chinese registration page explains project isolation and provides a project-name input', () => {
  const [source] = loadSource('../src/pages/RegisterPage.tsx');
  const translations = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
  const translate = key => key.split('.').reduce((value, part) => value?.[part], translations) || key;
  const scope = {
    exports: {}, require: name => {
      if (name === 'react') return { ...React, default: React };
      if (name === 'react-router-dom') return {
        useNavigate: () => () => {}, Navigate: () => null,
        Link: ({ to, children, ...props }) => React.createElement('a', { ...props, href: to }, children),
      };
      if (name === 'react-i18next') return { useTranslation: () => ({ t: translate }) };
      if (name === '@/stores') return { useAuthStore: () => ({ register() {}, isLoading: false, isAuthenticated: false }) };
      if (name === '@/services/api') return { APIError: class extends Error {}, RegistrationLoginError: class extends Error {} };
      throw new Error(`Unexpected dependency: ${name}`);
    },
  };
  compile(source, scope);
  const html = renderToStaticMarkup(React.createElement(scope.exports.default));
  assert.match(html, /项目名称/);
  assert.match(html, /name="workspaceName"/);
  assert.match(html, /客户数据互相隔离/);
  assert.match(html, /autoComplete="new-password"/);
  assert.match(html, /href="\/login"/);
});

test('login recovery displays and copies the same native command, never the old Docker command', async () => {
  const [source, tree] = loadSource('../src/pages/LoginPage.tsx');
  const command = findNode(tree, node => ts.isVariableDeclaration(node) && node.name.getText(tree) === 'passwordRecoveryCommand');
  const handler = findNode(tree, node => ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleCopyCommand');
  const copied = [];
  const scope = {
    navigator: { clipboard: { writeText: async text => copied.push(text) } },
    setCopied() {}, setTimeout() {}, console,
  };
  compile(`const passwordRecoveryCommand = ${command.initializer.getText(tree)};
    globalThis.copy = ${handler.initializer.getText(tree)};`, scope);
  await scope.copy({ preventDefault() {}, stopPropagation() {} });
  assert.equal(copied[0], 'powershell -NoProfile -File .\\scripts\\native-dev\\reset-password.ps1');
  assert.doesNotMatch(source, /docker exec|resetadmin/);
  assert.match(source, /\{passwordRecoveryCommand\}/);
  const translations = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
  assert.match(translations.auth.login.recoveryContact, /不支持邮件自助找回/);
});
