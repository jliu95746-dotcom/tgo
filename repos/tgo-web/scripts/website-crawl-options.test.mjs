import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/knowledgeBaseTransforms.ts', import.meta.url), 'utf8');
const scope = { exports: {}, require: name => {
  assert.equal(name, '@/i18n');
  return { default: { language: 'zh' } };
} };
vm.runInNewContext(ts.transpileModule(source.replaceAll('import.meta', '({ env: { DEV: false } })'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, scope);
const { transformCollectionToKnowledgeBaseItem: read, transformKnowledgeBaseItemToCreateRequest: write } = scope.exports;
const collection = crawl_config => ({ id: 'owned-fixture', display_name: '网页设置校验',
  collection_type: 'website', crawl_config, created_at: '2026-09-08', updated_at: '2026-09-08' });
const plain = value => JSON.parse(JSON.stringify(value));

test('crawl settings survive UI save and reload without field renaming or loss', () => {
  const options = { render_js: false, respect_robots_txt: false, wait_time: 2,
    delay_seconds: 0.25, timeout_seconds: 12, follow_external_links: true,
    user_agent: 'Owned/1', headers: { 'X-Fixture': 'owned' } };
  const request = write({ type: 'website', title: 'fixture', crawlConfig: {
    start_url: 'https://example.test/root', max_pages: 2, max_depth: 1, options,
  } });
  assert.deepEqual(plain(read(collection(request.crawl_config)).crawlConfig.options), options);
  assert.equal(request.crawl_config.wait_time, 2);
  assert.equal(request.crawl_config.delay_between_requests, undefined);
});

test('legacy saved fields remain readable; canonical false and zero take priority', () => {
  const legacy = read(collection({ js_rendering: true, delay_between_requests: 3, timeout: 9 }));
  assert.equal(legacy.crawlConfig.options.render_js, true);
  assert.equal(legacy.crawlConfig.options.wait_time, undefined);
  assert.equal(legacy.crawlConfig.options.delay_seconds, 3);
  assert.equal(legacy.crawlConfig.options.timeout_seconds, 9);
  const canonical = read(collection({ render_js: false, js_rendering: true,
    wait_time: 0, delay_between_requests: 3, timeout_seconds: 5, timeout: 9 }));
  assert.equal(canonical.crawlConfig.options.render_js, false);
  assert.equal(canonical.crawlConfig.options.wait_time, 0);
  assert.equal(canonical.crawlConfig.options.timeout_seconds, 5);
});
