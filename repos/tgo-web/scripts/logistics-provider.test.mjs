import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function service({ initialTools = [], selected = null, failLink = false } = {}) {
  let tools = initialTools;
  let settings = { query_tool_id: selected, enabled: false, archive_after_days: 60 };
  let creates = 0;
  const api = {
    getTools: async () => tools,
    createAiTool: async data => {
      creates++;
      const tool = { ...data, id: 'created' };
      tools = [...tools, tool];
      return tool;
    },
    updateAiTool: async (id, data) => {
      const tool = { ...tools.find(item => item.id === id), ...data };
      tools = tools.map(item => item.id === id ? tool : item);
      return tool;
    },
  };
  const logisticsApi = {
    getSettings: async () => settings,
    updateSettings: async data => {
      if (failLink) throw new Error('fixture link error');
      settings = data;
      return data;
    },
  };
  const scope = { exports: {}, crypto: { randomUUID: () => 'unique-test-id' }, require: name => {
    if (name === './projectToolsApi') return { default: api };
    if (name === './logisticsApi') return { logisticsApi };
    throw new Error(name);
  } };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/logisticsProviderService.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
  }).outputText, scope);
  return { ...scope.exports, settings: () => settings, creates: () => creates };
}

test('first configuration is automatically shared; opening from the other entrance does not create a copy', async () => {
  const api = service();
  const saved = await api.saveLogisticsProvider('project', null, 'https://provider.example/query', { provider_name: 'Fixture' });
  assert.equal(saved.linked, true);
  assert.equal(api.settings().query_tool_id, saved.tool.id);
  assert.equal(api.settings().enabled, false);
  assert.equal(api.settings().archive_after_days, 60);
  const loaded = await api.getLogisticsProviderTool();
  assert.equal(loaded.id, saved.tool.id);
  await api.saveLogisticsProvider('project', null, 'https://provider.example/query', { provider_name: 'Renamed' });
  assert.equal(api.creates(), 1);
  assert.equal((await api.getLogisticsProviderTool()).config.logistics_provider.provider_name, 'Renamed');
});

test('legacy tool record is reused even before archive binding exists', async () => {
  const api = service({ initialTools: [{ id: 'legacy', name: 'express_service' }] });
  const result = await api.saveLogisticsProvider('project', null, 'https://provider.example/query', { provider_name: 'Fixture' });
  assert.equal(result.tool.id, 'legacy');
  assert.equal(api.creates(), 0);
  assert.equal(api.settings().query_tool_id, 'legacy');
});

test('failed linking preserves saved ID and retry never creates a second record', async () => {
  const api = service({ failLink: true });
  const result = await api.saveLogisticsProvider('project', null, 'https://provider.example/query', { provider_name: 'Fixture' });
  assert.equal(result.linked, false);
  await api.saveLogisticsProvider('project', result.tool, 'https://provider.example/query', { provider_name: 'Fixture' });
  assert.equal(api.creates(), 1);
});
