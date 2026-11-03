import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

const filename = new URL('../src/utils/deviceScreenPolicy.ts', import.meta.url);
const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Number, RegExp });
const { prepareScreenPolicy, ownsDevicePolicy } = exports;
const local = {
  device_id: 'own-device', project_id: 'own-project', connection_instance_id: 'c'.repeat(32),
  grant: { protocol_version: 1, active: true, grant_id: 'a'.repeat(32), width: 640, height: 480,
    dpi: 120, max_actions: 10, used_actions: 2, allow_click: true, allow_text: true,
    click_regions: [{ x: 10, y: 10, width: 100, height: 50 }] },
};
const input = () => ({ deviceId: 'own-device', projectId: 'own-project', local,
  file: null, screen: null, maxActions: 5, allowClick: false, allowText: false, regions: [] });

test('screenshot scope starts without input permissions and uses current connection/version', () => {
  const result = prepareScreenPolicy(input());
  assert.equal(result.allow_click, false);
  assert.equal(result.allow_text, false);
  assert.equal(result.expected_policy_revision, 0);
  assert.equal(result.grant_id, local.grant.grant_id);
  assert.equal(result.connection_instance_id, local.connection_instance_id);
});
test('foreign scope, local stop, unretired file mode, and exhausted actions cannot publish', () => {
  for (const change of [
    { projectId: 'other' }, { deviceId: 'other' }, { maxActions: 2 }, { maxActions: 11 },
    { maxActions: 5.5 }, { maxActions: NaN },
    { local: { ...local, grant: { ...local.grant, active: false } } },
    { file: { device_id: 'own-device', project_id: 'own-project', status: 'pending' } },
    { screen: { device_id: 'other', project_id: 'own-project' } },
  ]) assert.equal(prepareScreenPolicy({ ...input(), ...change }), null);
});
test('click rectangles must be finite subregions; text cannot exceed the local permission', () => {
  const base = { ...input(), allowClick: true, regions: [{ x: 12, y: 15, width: 10, height: 10 }] };
  assert.ok(prepareScreenPolicy(base));
  for (const regions of [[], [{ x: 109, y: 10, width: 2, height: 1 }], [{ x: -1, y: 10, width: 2, height: 1 }], [{ x: 10, y: 10, width: 0, height: 1 }]]) {
    assert.equal(prepareScreenPolicy({ ...base, regions }), null);
  }
  assert.equal(prepareScreenPolicy({ ...input(), allowText: true,
    local: { ...local, grant: { ...local.grant, allow_text: false } } }), null);
});
test('retired file history permits explicit screen publication; stale ownership never does', () => {
  const own = { device_id: 'own-device', project_id: 'own-project', status: 'revoked' };
  assert.ok(prepareScreenPolicy({ ...input(), file: own }));
  assert.equal(prepareScreenPolicy({ ...input(), file: { ...own, project_id: 'foreign' } }), null);
  assert.equal(ownsDevicePolicy(own, 'own-device', 'own-project'), true);
});
