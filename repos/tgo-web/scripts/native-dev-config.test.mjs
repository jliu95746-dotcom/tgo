import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const moduleRequire = createRequire(import.meta.url);
const source = readFileSync(new URL('../vite.config.js', import.meta.url), 'utf8');

function config(flag) {
  const scope = { exports: {}, __dirname: 'test-web', process: { env: { TGO_DEV_TYPECHECK: flag } },
    require: name => {
      if (name === 'vite') return { defineConfig: value => value };
      if (name === '@vitejs/plugin-react') return () => ({ name: 'react' });
      if (name === '@tailwindcss/vite') return () => ({ name: 'tailwind' });
      if (name === 'vite-plugin-checker') return options => ({ name: 'checker', options });
      return moduleRequire(name);
    },
  };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, scope);
  return scope.exports.default;
}

test('native low-memory mode omits only the dev checker, not React or CSS', () => {
  const names = config('0').plugins.filter(Boolean).map(plugin => plugin.name);
  assert.ok(names.includes('react') && names.includes('tailwind'));
  assert.ok(!names.includes('checker'));
});

test('normal development and explicit opt-in retain the checker', () => {
  for (const flag of [undefined, '1']) {
    const checker = config(flag).plugins.find(plugin => plugin?.name === 'checker');
    assert.ok(checker && checker.options.typescript);
  }
});

test('manual typecheck and the build type gate remain unchanged', () => {
  const { scripts } = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  assert.equal(scripts['type-check'], 'tsc --noEmit');
  assert.equal(scripts.build, 'tsc && vite build');
});
