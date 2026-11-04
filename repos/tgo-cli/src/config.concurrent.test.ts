import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { spawn, type ChildProcess } from 'node:child_process';
import {
  existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync,
  rmdirSync, unlinkSync, utimesSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

describe('staff CLI config persistence across processes', () => {
  let root: string;
  let configPath: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'tgo-cli-config-test-'));
    configPath = join(root, 'config.json');
    vi.stubEnv('TGO_CONFIG_PATH', configPath);
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    if (!resolve(root).startsWith(resolve(tmpdir(), 'tgo-cli-config-test-'))) {
      throw new Error('Unexpected test directory');
    }
    rmSync(root, { recursive: true, force: true });
  });

  it('does not replace a damaged config with an empty object', async () => {
    writeFileSync(configPath, '{broken-json');
    const { updateConfig } = await import('./config.js');
    expect(() => updateConfig({ token: 'new-token' })).toThrow();
    expect(readFileSync(configPath, 'utf8')).toBe('{broken-json');
  });

  it('recovers a crashed process lock after the safety delay', async () => {
    const lock = `${configPath}.lock`;
    mkdirSync(lock);
    writeFileSync(join(lock, 'owner.json'), JSON.stringify({
      pid: 2147483000, nonce: 'terminated-process',
    }));
    const old = new Date(Date.now() - 60_000);
    utimesSync(lock, old, old);
    const { updateConfig } = await import('./config.js');
    updateConfig({ server: 'https://example.invalid' });
    expect(existsSync(lock)).toBe(false);
    expect(JSON.parse(readFileSync(configPath, 'utf8'))).toEqual({
      server: 'https://example.invalid',
    });
  });

  it('waits for the owner lock and preserves concurrent field updates', async () => {
    const lock = `${configPath}.lock`;
    mkdirSync(lock);
    const owner = join(lock, 'owner.json');
    writeFileSync(owner, JSON.stringify({ pid: process.pid, nonce: 'parent-lock' }));
    const moduleUrl = pathToFileURL(resolve('src/config.ts')).href;
    const script = 'const { updateConfig } = await import(process.argv[1]); updateConfig(JSON.parse(process.argv[2]));';
    const launch = (patch: object): ChildProcess => spawn(process.execPath, [
      '--experimental-strip-types', '--input-type=module', '-e', script,
      moduleUrl, JSON.stringify(patch),
    ], { cwd: resolve('.'), env: { ...process.env, TGO_CONFIG_PATH: configPath }, stdio: 'ignore' });

    const first = launch({ server: 'https://example.invalid' });
    const second = launch({ token: 'test-token' });
    const done = (child: ChildProcess) => new Promise<number>((resolveExit, reject) => {
      child.once('error', reject);
      child.once('exit', code => resolveExit(code ?? -1));
    });
    const firstDone = done(first);
    const secondDone = done(second);
    let bothWaiting = false;
    try {
      await new Promise(resolveWait => setTimeout(resolveWait, 250));
      bothWaiting = first.exitCode === null && second.exitCode === null;
    } finally {
      unlinkSync(owner);
      rmdirSync(lock);
    }
    const exitCodes = await Promise.all([firstDone, secondDone]);
    expect(bothWaiting).toBe(true);
    expect(exitCodes).toEqual([0, 0]);
    expect(JSON.parse(readFileSync(configPath, 'utf8'))).toEqual({
      server: 'https://example.invalid', token: 'test-token',
    });
  });
});
