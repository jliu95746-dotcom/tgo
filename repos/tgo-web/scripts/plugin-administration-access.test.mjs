import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/settings/PluginsSettings.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS, esModuleInterop: true,
    jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  },
}).outputText;

function load(role) {
  const exports = {};
  let storeReads = 0;
  const user = role ? { role, project_id: 'owned' } : null;
  const store = {
    installedPlugins: [], isLoadingInstalled: false, installedPluginsError: null,
    fetchInstalledPlugins() {},
  };
  vm.runInNewContext(compiled, {
    exports, console, setTimeout, clearTimeout,
    require(name) {
      if (name === '@/stores/authStore') return {
        useAuthStore: selector => selector ? selector({ user }) : { user },
      };
      if (name === '@/stores/pluginStore') return {
        usePluginStore: () => { storeReads++; return store; },
      };
      if (name === '@/hooks/useToast') return { useToast: () => ({}) };
      if (name === '@/utils/dateUtils') return { formatRelativeTime: () => '' };
      if (name === 'react-i18next') return {
        useTranslation: () => ({ t: (key, fallback) => fallback ?? key }),
      };
      if (name === 'react-router-dom') return {
        Navigate: ({ to, replace }) => React.createElement('span', {
          'data-redirect': to, 'data-replace': String(replace),
        }),
      };
      return require(name);
    },
  });
  return {
    render: () => renderToStaticMarkup(React.createElement(exports.default)),
    reads: () => storeReads,
  };
}

for (const role of ['user', 'agent', undefined]) {
  test(`role ${role} cannot mount the plugin management page`, () => {
    const page = load(role);
    const markup = page.render();
    assert.match(markup, /data-redirect="\/settings\/profile"/);
    assert.match(markup, /data-replace="true"/);
    assert.equal(page.reads(), 0);
    assert.doesNotMatch(markup, /插件管理|安装插件/);
  });
}

test('administrator keeps the existing plugin management content', () => {
  const page = load('admin');
  const markup = page.render();
  assert.match(markup, /插件管理/);
  assert.match(markup, /安装插件/);
  assert.doesNotMatch(markup, /data-redirect/);
  assert.equal(page.reads(), 1);
});
