import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';

describe('config', () => {
  let root: string;
  let configPath: string;
  let config: typeof import('./config.js');

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), 'tgo-cli-config-test-'));
    configPath = join(root, '.tgo', 'config.json');
    vi.stubEnv('TGO_CONFIG_PATH', configPath);
    vi.resetModules();
    config = await import('./config.js');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    if (!resolve(root).startsWith(resolve(tmpdir(), 'tgo-cli-config-test-'))) {
      throw new Error('Unexpected test directory');
    }
    rmSync(root, { recursive: true, force: true });
  });

  function write(value: string): void {
    mkdirSync(dirname(configPath), { recursive: true });
    writeFileSync(configPath, value);
  }

  describe('loadConfig', () => {
    it('returns an empty object when the file does not exist', () => {
      expect(config.loadConfig()).toEqual({});
    });

    it('reads a valid config file', () => {
      write(JSON.stringify({ server: 'http://localhost:8000/api', token: 'abc' }));
      expect(config.loadConfig()).toEqual({ server: 'http://localhost:8000/api', token: 'abc' });
    });

    it('returns an empty object for invalid JSON during read', () => {
      write('not json');
      expect(config.loadConfig()).toEqual({});
    });
  });

  describe('saveConfig', () => {
    it('creates the directory and writes a complete config', () => {
      config.saveConfig({ server: 'http://test', token: 'tok' });
      expect(existsSync(dirname(configPath))).toBe(true);
      expect(readFileSync(configPath, 'utf8')).toBe(
        JSON.stringify({ server: 'http://test', token: 'tok' }, null, 2) + '\n',
      );
    });
  });

  describe('updateConfig', () => {
    it('merges a patch with the current config', () => {
      write(JSON.stringify({ server: 'http://old' }));
      config.updateConfig({ token: 'new-token' });
      expect(config.loadConfig()).toEqual({ server: 'http://old', token: 'new-token' });
    });

    it('removes keys set to undefined', () => {
      write(JSON.stringify({ server: 'http://s', token: 'tok' }));
      config.updateConfig({ token: undefined });
      expect(config.loadConfig()).toEqual({ server: 'http://s' });
    });
  });

  describe('resolveServer', () => {
    it('prefers the CLI flag', () => {
      vi.stubEnv('TGO_SERVER', 'http://env');
      write(JSON.stringify({ server: 'http://config' }));
      expect(config.resolveServer('http://flag')).toBe('http://flag');
    });

    it('uses the environment when no flag was passed', () => {
      vi.stubEnv('TGO_SERVER', 'http://env');
      expect(config.resolveServer()).toBe('http://env');
    });

    it('falls back to the config file', () => {
      write(JSON.stringify({ server: 'http://config' }));
      expect(config.resolveServer()).toBe('http://config');
    });

    it('returns undefined when no server is configured', () => {
      expect(config.resolveServer()).toBeUndefined();
    });
  });

  describe('resolveToken', () => {
    it('prefers the CLI flag', () => {
      vi.stubEnv('TGO_TOKEN', 'env-tok');
      expect(config.resolveToken('flag-tok')).toBe('flag-tok');
    });

    it('uses the environment when no flag was passed', () => {
      vi.stubEnv('TGO_TOKEN', 'env-tok');
      expect(config.resolveToken()).toBe('env-tok');
    });
  });

  describe('resolveOutput', () => {
    it('defaults to json', () => {
      expect(config.resolveOutput()).toBe('json');
    });

    it('accepts table', () => {
      expect(config.resolveOutput('table')).toBe('table');
    });

    it('accepts compact', () => {
      expect(config.resolveOutput('compact')).toBe('compact');
    });

    it('falls back to json for unknown values', () => {
      expect(config.resolveOutput('xml')).toBe('json');
    });

    it('reads the environment', () => {
      vi.stubEnv('TGO_OUTPUT', 'table');
      expect(config.resolveOutput()).toBe('table');
    });
  });
});
