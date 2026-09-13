import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const sourceRoot = new URL('../src/', import.meta.url);

test('visitor presentation types live in a type-only module without mock dependencies', () => {
  const source = readFileSync(new URL('types/visitor.ts', sourceRoot), 'utf8');
  const tree = ts.createSourceFile('visitor.ts', source, ts.ScriptTarget.Latest, true);
  const names = tree.statements.filter(ts.isInterfaceDeclaration).map(node => node.name.text);
  assert.deepEqual(names, [
    'CustomAttribute', 'VisitorBasicInfo', 'VisitorEmotion', 'VisitorAIInsights',
    'VisitorSystemInfo', 'VisitorActivity', 'VisitorTicket', 'AIPersonaTag', 'VisitorTag', 'ExtendedVisitor',
  ]);
  for (const node of tree.statements.filter(ts.isImportDeclaration)) {
    assert.equal(node.importClause.isTypeOnly, true);
    assert.doesNotMatch(node.moduleSpecifier.text, /data\/mock/);
  }
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext },
  }).outputText;
  assert.equal(compiled.trim(), 'export {};', 'types must not introduce runtime data');
});

test('production components do not import mock modules even for visitor types', () => {
  function check(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const file = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory);
      if (entry.isDirectory()) { check(file); continue; }
      if (!/\.tsx?$/.test(entry.name)) continue;
      const tree = ts.createSourceFile(entry.name, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
      for (const node of tree.statements.filter(ts.isImportDeclaration)) {
        assert.doesNotMatch(node.moduleSpecifier.text, /data\/mock/, file.pathname);
      }
    }
  }
  check(new URL('components/', sourceRoot));
});
