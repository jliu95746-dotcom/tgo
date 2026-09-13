import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const changedFiles = [
  'types/index.ts', 'utils/platformUtils.ts', 'pages/PlatformConfigPage.tsx',
  'services/platformsApi.ts', 'services/customerMessageDelivery.ts',
  'components/chat/MessageInput.tsx', 'components/layout/ChatWindow.tsx',
];

for (const path of changedFiles) {
  test(`${path} has no personal-WeChat entry or syntax errors`, () => {
    const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /wechat_personal|WECHAT_PERSONAL|WeChatPersonal|VisionAgentConsole/);
    const result = ts.transpileModule(source, {
      fileName: path,
      reportDiagnostics: true,
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    });
    assert.deepEqual(result.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error), []);
  });
}

test('dedicated configuration and console source files are removed', () => {
  for (const component of ['WeChatPersonalPlatformConfig', 'VisionAgentConsole']) {
    assert.equal(existsSync(new URL(`../src/components/platforms/${component}.tsx`, import.meta.url)), false);
  }
});

test('other WeChat channels remain selectable', () => {
  const source = readFileSync(new URL('../src/types/index.ts', import.meta.url), 'utf8');
  for (const name of ['WECHAT', 'WECOM', 'WECOM_BOT']) {
    assert.match(source, new RegExp(`\\b${name} =`));
  }
});
