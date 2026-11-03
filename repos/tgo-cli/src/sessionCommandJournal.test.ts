import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, utimesSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { SessionCommandJournal, makeSessionCommandScope } from './sessionCommandJournal.js';

const temporaryRoots: string[] = [];
function temporaryRoot(): string {
  const root = mkdtempSync(join(process.cwd(), '.tmp-session-command-journal-'));
  temporaryRoots.push(root);
  return root;
}

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    if (!resolve(root).startsWith(resolve(process.cwd()) + sep)) {
      throw new Error('Refusing to remove a test path outside the CLI checkout');
    }
    rmSync(root, { recursive: true, force: true });
  }
});

const scope = makeSessionCommandScope(
  'https://api.example.test/',
  '2458e650-22be-4a95-b4bc-aa43fe72ed54',
  '14db1238-b44b-4f1a-b48b-51e2b3715267',
  '3e7d710c-b8b0-4d63-9758-7932775fbd46',
);

describe('SessionCommandJournal', () => {
  it('converges two CLI instances on one stored operation id', async () => {
    const root = temporaryRoot();
    const first = new SessionCommandJournal(root);
    const second = new SessionCommandJournal(root);
    const [left, right] = await Promise.all([
      first.reserve(scope, 'close', null),
      second.reserve(scope, 'close', null),
    ]);
    expect(left.record.operationId).toBe(right.record.operationId);
    expect([left.created, right.created].sort()).toEqual([false, true]);
    const stored = readFileSync(join(root, readdirSync(root).find(name => name.endsWith('.json'))!), 'utf8');
    expect(stored).not.toContain('token');
    expect(stored).not.toContain('password');
  });

  it('isolates actor and server scopes for the same visitor', async () => {
    const journal = new SessionCommandJournal(temporaryRoot());
    const first = await journal.reserve(scope, 'close', null);
    const otherActor = makeSessionCommandScope(
      'https://api.example.test', scope.projectId,
      '4e6fb629-ccee-483e-b684-36f4d9dad53a', scope.visitorId,
    );
    const otherServer = makeSessionCommandScope(
      'https://elsewhere.example.test', scope.projectId, scope.actorId, scope.visitorId,
    );
    expect((await journal.reserve(otherActor, 'close', null)).record.operationId).not.toBe(first.record.operationId);
    expect((await journal.reserve(otherServer, 'close', null)).record.operationId).not.toBe(first.record.operationId);
    expect(await journal.read(scope)).toEqual(first.record);
  });

  it('refuses a new target or identifier while the original is unresolved', async () => {
    const journal = new SessionCommandJournal(temporaryRoot());
    const first = await journal.reserve(scope, 'transfer', '4e6fb629-ccee-483e-b684-36f4d9dad53a');
    await expect(journal.reserve(scope, 'close', null)).rejects.toThrow();
    await expect(journal.reserve(scope, 'transfer', '4e6fb629-ccee-483e-b684-36f4d9dad53a', {
      operationId: 'd9503a7a-91e1-4f6a-8da3-cc31ff535d21',
    })).rejects.toThrow();
    await expect(journal.mark(scope, 'd9503a7a-91e1-4f6a-8da3-cc31ff535d21', 'committed')).rejects.toThrow();
    expect((await journal.read(scope))?.operationId).toBe(first.record.operationId);
  });

  it('retains a completed tombstone until a deliberate new operation', async () => {
    const journal = new SessionCommandJournal(temporaryRoot());
    const first = await journal.reserve(scope, 'close', null);
    await journal.mark(scope, first.record.operationId, 'committed');
    expect((await journal.reserve(scope, 'close', null)).created).toBe(false);
    const next = await journal.reserve(scope, 'close', null, { newOperation: true });
    expect(next.created).toBe(true);
    expect(next.record.operationId).not.toBe(first.record.operationId);
  });

  it('fails closed on corrupt state instead of overwriting it', async () => {
    const root = temporaryRoot();
    const journal = new SessionCommandJournal(root);
    await journal.reserve(scope, 'close', null);
    const path = join(root, readdirSync(root).find(name => name.endsWith('.json'))!);
    writeFileSync(path, '{broken');
    await expect(journal.reserve(scope, 'close', null)).rejects.toThrow();
  });

  it('rejects unexpected fields instead of carrying secrets into a later write', async () => {
    const root = temporaryRoot();
    const journal = new SessionCommandJournal(root);
    await journal.reserve(scope, 'close', null);
    const path = join(root, readdirSync(root).find(name => name.endsWith('.json'))!);
    writeFileSync(path, JSON.stringify({ ...JSON.parse(readFileSync(path, 'utf8')), token: 'must-not-copy' }));
    await expect(journal.reserve(scope, 'close', null)).rejects.toThrow();
  });

  it('recovers an abandoned lock without deleting another command record', async () => {
    const root = temporaryRoot();
    const journal = new SessionCommandJournal(root);
    await journal.reserve(scope, 'close', null);
    const lock = join(root, readdirSync(root).find(name => name.endsWith('.json'))!.replace('.json', '.lock'));
    mkdirSync(lock);
    writeFileSync(join(lock, 'owner.json'), JSON.stringify({ pid: 2147483000, nonce: 'dead', createdAt: 1 }));
    const old = new Date(Date.now() - 120_000);
    utimesSync(lock, old, old);
    expect(statSync(lock).mtimeMs).toBeLessThan(Date.now() - 30_000);
    expect((await journal.reserve(scope, 'close', null)).created).toBe(false);
    expect(await journal.read(scope)).not.toBeNull();
  });
});
