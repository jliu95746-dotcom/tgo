import {
  closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  renameSync, rmdirSync, statSync, unlinkSync, writeFileSync,
} from 'node:fs';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export interface TgoConfig {
  server?: string;
  token?: string;
  auth_login_request_id?: string;
  output?: 'json' | 'table' | 'compact';
}

const CONFIG_DIR = join(homedir(), '.tgo');
const CONFIG_PATH = process.env.TGO_CONFIG_PATH || join(CONFIG_DIR, 'config.json');
const LOCK_STALE_MS = 30_000;
const LOCK_ATTEMPTS = 50;
const LOCK_WAIT_MS = 50;

function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

function recoverStaleLock(lock: string): boolean {
  try {
    if (Date.now() - statSync(lock).mtimeMs < LOCK_STALE_MS) return false;
    const ownerPath = join(lock, 'owner.json');
    const ownerText = existsSync(ownerPath) ? readFileSync(ownerPath, 'utf8') : null;
    let owner: { pid?: unknown } | null = null;
    if (ownerText !== null) {
      try {
        owner = JSON.parse(ownerText) as { pid?: unknown };
      } catch {
        owner = null;
      }
    }
    if (owner && typeof owner.pid === 'number' && processAlive(owner.pid)) return false;
    const abandoned = `${lock}.abandoned-${randomUUID()}`;
    renameSync(lock, abandoned);
    const movedOwner = join(abandoned, 'owner.json');
    const movedText = existsSync(movedOwner) ? readFileSync(movedOwner, 'utf8') : null;
    if (movedText !== ownerText) throw new Error('配置锁在回收期间发生变化，请人工核对');
    if (existsSync(movedOwner)) unlinkSync(movedOwner);
    rmdirSync(abandoned);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return true;
    throw error;
  }
}

function withConfigLock<T>(action: () => T): T {
  mkdirSync(dirname(CONFIG_PATH), { recursive: true, mode: 0o700 });
  const lock = `${CONFIG_PATH}.lock`;
  const ownerPath = join(lock, 'owner.json');
  const nonce = randomUUID();
  let acquired = false;
  const wait = new Int32Array(new SharedArrayBuffer(4));
  for (let attempt = 0; attempt < LOCK_ATTEMPTS; attempt++) {
    try {
      mkdirSync(lock, { mode: 0o700 });
      acquired = true;
      writeFileSync(ownerPath, JSON.stringify({ pid: process.pid, nonce }), { mode: 0o600 });
      break;
    } catch (error) {
      if (acquired) {
        if (existsSync(ownerPath)) unlinkSync(ownerPath);
        rmdirSync(lock);
        throw error;
      }
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
      if (recoverStaleLock(lock)) continue;
      Atomics.wait(wait, 0, 0, LOCK_WAIT_MS);
    }
  }
  if (!acquired) throw new Error('配置文件正被其他进程使用，请稍后重试');
  try {
    return action();
  } finally {
    const owner = JSON.parse(readFileSync(ownerPath, 'utf8')) as { nonce?: string };
    if (owner.nonce !== nonce) throw new Error('配置锁归属变化，请人工核对');
    unlinkSync(ownerPath);
    rmdirSync(lock);
  }
}

function readConfigForUpdate(): TgoConfig {
  if (!existsSync(CONFIG_PATH)) return {};
  try {
    const value: unknown = JSON.parse(readFileSync(CONFIG_PATH, 'utf8'));
    if (value && typeof value === 'object' && !Array.isArray(value)) return value as TgoConfig;
  } catch {
    // A malformed file must be preserved for recovery, not silently overwritten.
  }
  throw new Error('本地配置文件损坏，请保留原文件并人工核对');
}

function writeConfigAtomically(config: TgoConfig): void {
  const temporary = `${CONFIG_PATH}.${randomUUID()}.tmp`;
  try {
    const fd = openSync(temporary, 'wx', 0o600);
    try {
      writeFileSync(fd, JSON.stringify(config, null, 2) + '\n');
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    renameSync(temporary, CONFIG_PATH);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}

export function loadConfig(): TgoConfig {
  if (!existsSync(CONFIG_PATH)) return {};
  try {
    return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8'));
  } catch {
    return {};
  }
}

export function saveConfig(config: TgoConfig): void {
  withConfigLock(() => writeConfigAtomically(config));
}

export function updateConfig(patch: Partial<TgoConfig>): void {
  withConfigLock(() => {
    const config = readConfigForUpdate();
    Object.assign(config, patch);
    for (const key of Object.keys(config) as (keyof TgoConfig)[]) {
      if (config[key] == null) delete config[key];
    }
    writeConfigAtomically(config);
  });
}

export function beginAuthLogin(): string {
  const requestId = randomUUID();
  withConfigLock(() => {
    const config = readConfigForUpdate();
    config.auth_login_request_id = requestId;
    writeConfigAtomically(config);
  });
  return requestId;
}

export function completeAuthLogin(requestId: string, server: string, token: string): boolean {
  return withConfigLock(() => {
    const config = readConfigForUpdate();
    if (config.auth_login_request_id !== requestId) return false;
    config.server = server;
    config.token = token;
    delete config.auth_login_request_id;
    writeConfigAtomically(config);
    return true;
  });
}

export function cancelAuthLogin(requestId: string): void {
  withConfigLock(() => {
    const config = readConfigForUpdate();
    if (config.auth_login_request_id !== requestId) return;
    delete config.auth_login_request_id;
    writeConfigAtomically(config);
  });
}

export function clearAuthSession(): void {
  withConfigLock(() => {
    const config = readConfigForUpdate();
    delete config.token;
    delete config.auth_login_request_id;
    writeConfigAtomically(config);
  });
}

/** Resolve a setting with priority: CLI flag > env var > config file */
export function resolveServer(flag?: string): string | undefined {
  return flag || process.env.TGO_SERVER || loadConfig().server;
}

export function resolveToken(flag?: string): string | undefined {
  return flag || process.env.TGO_TOKEN || loadConfig().token;
}

export function resolveOutput(flag?: string): 'json' | 'table' | 'compact' {
  const v = flag || process.env.TGO_OUTPUT || loadConfig().output;
  if (v === 'table' || v === 'compact') return v;
  return 'json';
}
