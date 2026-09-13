import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');

test('employee page explains its purpose with one subtitle and no guide', () => {
  const page = read('components/ai/AgentManagement.tsx');
  const zh = JSON.parse(read('i18n/locales/zh.json'));
  assert.equal(zh.agents.subtitle, '为不同品牌或产品，配置各自的专职客服');
  assert.match(page, /t\('agents\.subtitle'/);
  assert.doesNotMatch(page, /AgentPurposeGuide|agents\.guide/);
  assert.equal(zh.agents.guide, undefined);
  assert.equal(existsSync(new URL('../src/components/ai/AgentPurposeGuide.tsx', import.meta.url)), false);
});
