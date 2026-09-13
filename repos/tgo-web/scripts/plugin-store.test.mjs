import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/stores/pluginStore.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText;

function loadStore(listInstalledPlugins) {
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, Error,
    console: { error() {}, log() {} },
    require: (name) => {
      if (name === '@/services/pluginApi') return { pluginApiService: { listInstalledPlugins } };
      if (name === 'i18next') return { t: (_key, fallback) => fallback };
      return require(name);
    },
  });
  return exports.usePluginStore;
}

test('a failed plugin request is an error, not an empty successful result', async () => {
  const store = loadStore(async () => { throw new Error('HTTP 502: plugin runtime unavailable'); });
  await store.getState().fetchInstalledPlugins();
  assert.equal(store.getState().installedPluginsError, 'HTTP 502: plugin runtime unavailable');
  assert.equal(store.getState().isLoadingInstalled, false);
});

test('retry clears the previous error and keeps the successful response', async () => {
  let fail = true;
  const plugins = [{ plugin_id: 'verification-plugin' }];
  const store = loadStore(async () => {
    if (fail) throw new Error('unavailable');
    return { plugins };
  });
  await store.getState().fetchInstalledPlugins();
  fail = false;
  await store.getState().fetchInstalledPlugins();
  assert.equal(store.getState().installedPluginsError, null);
  assert.equal(store.getState().installedPlugins, plugins);
  assert.equal(store.getState().isLoadingInstalled, false);
});

test('a successful empty response is distinguishable from a failure', async () => {
  const store = loadStore(async () => ({ plugins: [] }));
  await store.getState().fetchInstalledPlugins();
  assert.equal(store.getState().installedPluginsError, null);
  assert.equal(store.getState().installedPlugins.length, 0);
});
