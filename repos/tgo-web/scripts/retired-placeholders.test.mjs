import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import ts from 'typescript';

const sourceRoot = fileURLToPath(new URL('../src/', import.meta.url));
const retired = [
  'pages/SettingsPage.tsx',
  'pages/ComingSoonPage.tsx',
  'components/settings/StorageSettings.tsx',
  'components/settings/ShortcutsSettings.tsx',
  'components/settings/CalendarSettings.tsx',
  'stores/toolsStoreOptimized.ts',
];

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : /\.[jt]sx?$/.test(path) ? [path] : [];
  });
}

test('unused placeholder modules are retired, not kept as alternative implementations', () => {
  for (const path of retired) assert.equal(existsSync(resolve(sourceRoot, path)), false, path);
});

test('remaining source has no import, export or dynamic module reference to retired modules', () => {
  const names = retired.map(path => path.split('/').at(-1).replace(/\.[^.]+$/, ''));
  for (const path of sourceFiles(sourceRoot)) {
    const tree = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true);
    function visit(node) {
      if (ts.isStringLiteralLike(node)) {
        const lastSegment = node.text.replace(/\.[jt]sx?$/, '').split('/').at(-1);
        assert.ok(!names.includes(lastSegment), `${relative(sourceRoot, path)}: ${node.text}`);
      }
      ts.forEachChild(node, visit);
    }
    visit(tree);
  }
});

test('real settings and tool routes remain wired to their existing implementations', () => {
  const router = readFileSync(resolve(sourceRoot, 'router/index.tsx'), 'utf8');
  const routes = {
    profile: 'ProfileSettings', general: 'GeneralSettings', notifications: 'NotificationSettings',
    staff: 'StaffSettings', providers: 'ModelProvidersSettings', plugins: 'PluginsSettings',
    logistics: 'LogisticsSettings', about: 'AboutSettings', tools: 'Tools',
  };
  for (const [path, component] of Object.entries(routes)) {
    assert.match(router, new RegExp(`path: ['"]${path}['"],\\s*element: <${component}\\s*/>`));
  }
  const general = readFileSync(resolve(sourceRoot, 'components/settings/GeneralSettings.tsx'), 'utf8');
  assert.match(general, /<LanguageSelector\b/);
  assert.match(general, /setThemeMode\(option.value\)/);
  const sidebar = readFileSync(resolve(sourceRoot, 'components/settings/SettingsSidebar.tsx'), 'utf8');
  assert.match(sidebar, /await logout\(\)/);
  assert.ok(existsSync(resolve(sourceRoot, 'components/ai/AddToolModal.tsx')));
  assert.ok(existsSync(resolve(sourceRoot, 'components/ai/AddHTTPToolModal.tsx')));
});

test('unused alert-only file preview is retired while real document operations remain', () => {
  const service = readFileSync(resolve(sourceRoot, 'services/fileManagementService.ts'), 'utf8');
  assert.doesNotMatch(service, /\bpreviewFile\b|此功能正在开发中/);
  const detail = readFileSync(resolve(sourceRoot, 'pages/KnowledgeBaseDetail.tsx'), 'utf8');
  assert.match(detail, /fileServiceRef\.current\.loadFiles\(/);
  assert.match(detail, /fileServiceRef\.current\.uploadFiles\(/);
  assert.match(detail, /KnowledgeBaseApiService\.downloadFile\(/);
  assert.match(detail, /KnowledgeBaseApiService\.deleteFile\(/);
  for (const path of sourceFiles(sourceRoot)) {
    assert.doesNotMatch(readFileSync(path, 'utf8'), /\bpreviewFile\b/, relative(sourceRoot, path));
  }
});
