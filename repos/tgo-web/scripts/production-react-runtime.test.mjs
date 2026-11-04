import assert from 'node:assert/strict';
import { readdir } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import test from 'node:test';

test('production React chunk initializes before icon consumers', async () => {
  const assets = fileURLToPath(new URL('../dist/assets/', import.meta.url));
  const files = await readdir(assets);
  const reactChunk = files.find((name) => /^vendor-react-[^.]+\.js$/.test(name));
  assert.ok(reactChunk, 'build the production bundle before this check');
  const exports = await import(pathToFileURL(path.join(assets, reactChunk)).href);
  const react = Object.values(exports).find((value) => (
    value && typeof value.forwardRef === 'function' && typeof value.createElement === 'function'
  ));
  assert.ok(react, 'React must initialize without importing application components');
});
