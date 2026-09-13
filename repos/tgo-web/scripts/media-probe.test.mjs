import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const read = file => readFileSync(new URL(file, import.meta.url), 'utf8');
const scope = { exports: {} };
vm.runInNewContext(ts.transpileModule(read('../src/utils/mediaProbe.ts'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, scope);
const { mediaProbeFileError } = scope.exports;
test('valid samples require matching types and bounded nonempty bytes', () => {
  assert.equal(mediaProbeFileError({ type: 'audio/wav', size: 100 }, 'asr'), null);
  assert.equal(mediaProbeFileError({ type: 'image/png', size: 100 }, 'ocr'), null);
  assert.equal(mediaProbeFileError({ type: 'image/png', size: 100 }, 'vlm'), null);
  assert.equal(mediaProbeFileError({ type: 'image/png', size: 100 }, 'asr'), 'type');
  assert.equal(mediaProbeFileError({ type: 'audio/wav', size: 0 }, 'asr'), 'size');
  assert.equal(mediaProbeFileError({ type: 'image/png', size: 6 * 1024 * 1024 }, 'ocr'), 'size');
});
test('card allows media test and opens selected model, not project defaults', () => {
  assert.doesNotMatch(read('../src/components/settings/ProviderCard.tsx'), /!\['chat', 'embedding'\]\.includes/);
  assert.match(read('../src/components/settings/ModelProvidersSettings.tsx'), /setMediaTarget\(\{ providerId: p.id, providerName: p.name, modelId, capability: modelType \}\)/);
  const modal = read('../src/components/settings/MediaModelTestModal.tsx');
  assert.match(modal, /!confirmed/);
  assert.match(modal, /probeMediaModel\(target, file\)/);
  assert.doesNotMatch(modal, /dangerouslySetInnerHTML|upsertAIConfig|sendMessage/);
});
