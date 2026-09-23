import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const fileId = '11111111-1111-4111-8111-111111111111';
const fileUrl = `https://files.example.test/v1/chat/files/${fileId}?file_token=expired`;
function load(path, imports) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const scope = { exports: {}, URL, window: { location: { origin: 'https://company.example.test' } },
    require: name => imports[name] ?? {} };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  } }).outputText, scope);
  return scope.exports;
}

test('staff renews historical links through its own API and caches only within the session', async () => {
  let token = 'company-A';
  const calls = [];
  const apiClient = { getToken: () => token, post: async endpoint => {
    calls.push({ endpoint, token });
    return { access_url: `https://api.example.test/${token}`, expires_at: new Date(Date.now() + 300000).toISOString() };
  } };
  const service = load('../src/services/chatFileAccess.ts', { './api': { apiClient } });
  assert.equal(await service.authorizeChatFileUrl(fileUrl), 'https://api.example.test/company-A');
  await service.authorizeChatFileUrl(fileUrl);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].endpoint, `/v1/chat/files/${fileId}/access`);
  token = 'company-B';
  assert.equal(await service.authorizeChatFileUrl(fileUrl), 'https://api.example.test/company-B');
  assert.equal(calls.length, 2);
  assert.equal(await service.authorizeChatFileUrl('blob:local-preview'), 'blob:local-preview');
  assert.equal(calls.length, 2);
});

test('expired staff file links are refreshed and denied requests never retain an old link', async () => {
  let calls = 0;
  const apiClient = { getToken: () => 'company-A', post: async () => {
    calls += 1;
    if (calls > 1) throw new Error('permission revoked');
    return { access_url: 'short-lived', expires_at: new Date(Date.now() + 1000).toISOString() };
  } };
  const service = load('../src/services/chatFileAccess.ts', { './api': { apiClient } });
  assert.equal(await service.authorizeChatFileUrl(fileUrl), 'short-lived');
  await assert.rejects(service.authorizeChatFileUrl(fileUrl));
  assert.equal(calls, 2);
});

test('widget sends its platform key only to the configured API, including markdown images', () => {
  const upload = load('../../tgo-widget-js/src/services/upload.ts', { '../utils/url': { resolveApiKey: () => null } });
  const service = load('../../tgo-widget-js/src/services/chatFileAccess.ts', { './upload': upload });
  const authorized = service.authorizeChatFileUrl(fileUrl, 'https://api.example.test', 'widget-key');
  const result = new URL(authorized);
  assert.equal(result.origin, 'https://api.example.test');
  assert.equal(result.searchParams.get('platform_api_key'), 'widget-key');
  assert.equal(result.searchParams.has('file_token'), false);
  assert.equal(service.authorizeChatFileUrl(fileUrl), '');
  const external = 'https://photos.example.test/image.png';
  assert.equal(service.authorizeChatFileUrl(external, 'https://api.example.test', 'widget-key'), external);
  const markdown = service.authorizeChatMarkdown(`![图片](${fileUrl})`, 'https://api.example.test', 'widget-key');
  assert.ok(markdown.includes('https://api.example.test/v1/chat/files/'));
  assert.ok(!markdown.includes('files.example.test'));
});
