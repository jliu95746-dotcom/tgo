import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
const device = { id: 'device-a', project_id: 'project-a', status: 'online' };

function render({ readTestError = false, testingRead = false } = {}) {
  const values = {
    selectedId: device.id, checkedId: device.id, draftSaved: true,
    targetDirectory: '/docs', readTestError, testingRead,
    readPolicy: { allowed_directory: '/docs', max_lines: 100, max_offset: 10000,
      status: 'active', verified: true },
  };
  // Seed named hook state to render a previous success followed by a new request.
  const source = readFileSync(new URL('../src/components/business/DeviceScenarioGuide.tsx', import.meta.url), 'utf8')
    .replace(/const \[(\w+), (\w+)\] = useState(?:<[^;]+>)?\([^;]+\);/g,
      (statement, value, setter) => value in values
        ? `const [${value}, ${setter}] = [fixtureValues.${value}, () => {}];` : statement);
  const output = {};
  const dependencies = {
    'react-i18next': { useTranslation: () => ({ t: key => key }) },
    'react-router-dom': { useNavigate: () => () => {} },
    '@/hooks/useDialogFocus': { useDialogFocus: () => ({ current: null }) },
    '@/stores/authStore': { useAuthStore: selector => selector({ user }) },
    '@/stores/aiStore': { useAIStore: {} },
    '@/stores/deviceSetupStore': { useDeviceSetupStore: {} },
    '@/stores/deviceControlStore': { useDeviceControlStore: () => ({
      devices: [device], isLoading: false, loadError: null, hasLoadedDevices: true,
    }) },
    '@/services/deviceControlApi': {}, '@/services/api': {},
    '@/utils/deviceSetup': { supportsDeviceScenario: () => true },
    './DeviceScriptPanel': { default: () => null, __esModule: true },
  };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, { exports: output, fixtureValues: values, AbortController,
    require: name => dependencies[name] ?? require(name) });
  return renderToStaticMarkup(React.createElement(output.default, {
    scenarioId: 'files', initialDeviceId: device.id, onClose() {}, onConnect() {},
  }));
}

const continueButton = html => html.match(/<button\b[^>]*>businessScenarios\.deviceGuide\.continue<\/button>/)?.[0];

test('a previous verified connection can continue when no newer test failed', () => {
  const html = render();
  assert.match(html, /businessScenarios\.deviceGuide\.readTestPassed/);
  assert.ok(continueButton(html));
  assert.doesNotMatch(continueButton(html), /\sdisabled=/);
});

test('a rejected latest read test hides stale success and blocks employee handoff', () => {
  const html = render({ readTestError: true });
  assert.match(html, /businessScenarios\.deviceGuide\.readTestError/);
  assert.equal(html.includes('businessScenarios.deviceGuide.readTestPassed'), false, 'rejected request still shows success');
  assert.match(continueButton(html), /\sdisabled=/);
});

test('a running new test does not display the previous success as its result', () => {
  const html = render({ testingRead: true });
  assert.equal(html.includes('businessScenarios.deviceGuide.readTestPassed'), false, 'running request still shows success');
  assert.match(continueButton(html), /\sdisabled=/);
});
