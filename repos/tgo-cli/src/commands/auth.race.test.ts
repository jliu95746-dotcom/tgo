import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

describe('staff auth network races', () => {
  let root: string;
  let server: Server;
  let serverUrl: string;
  let releaseSlow: () => void;
  let slowStarted: Promise<void>;
  let auth: typeof import('./auth.js');

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), 'tgo-cli-auth-race-'));
    vi.stubEnv('TGO_CONFIG_PATH', join(root, 'config.json'));
    vi.resetModules();
    let signalSlow: () => void = () => {};
    slowStarted = new Promise(resolveStarted => { signalSlow = resolveStarted; });
    const slowGate = new Promise<void>(resolveGate => { releaseSlow = resolveGate; });
    server = createServer(async (request, response) => {
      let input = '';
      for await (const chunk of request) input += chunk;
      const username = new URLSearchParams(input).get('username');
      if (request.url !== '/v1/staff/login' || !username) {
        response.writeHead(404).end();
        return;
      }
      if (username === 'slow') {
        signalSlow();
        await slowGate;
      }
      response.setHeader('Content-Type', 'application/json');
      if (username === 'invalid') {
        response.writeHead(401).end(JSON.stringify({ message: 'Invalid credentials' }));
        return;
      }
      response.end(JSON.stringify({
        access_token: `token-${username}`,
        token_type: 'bearer',
        staff: { id: username },
      }));
    });
    await new Promise<void>(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Missing HTTP fixture port');
    serverUrl = `http://127.0.0.1:${address.port}`;
    auth = await import('./auth.js');
  });

  afterEach(async () => {
    releaseSlow();
    server.closeAllConnections();
    await new Promise<void>(resolveClose => server.close(() => resolveClose()));
    vi.unstubAllEnvs();
    vi.resetModules();
    if (!resolve(root).startsWith(resolve(tmpdir(), 'tgo-cli-auth-race-'))) {
      throw new Error('Unexpected test directory');
    }
    rmSync(root, { recursive: true, force: true });
  });

  it('does not restore a login after logout while its HTTP response is delayed', async () => {
    const pendingLogin = auth.authLogin(serverUrl, 'slow', 'fixture-password');
    await slowStarted;
    auth.authLogout();
    releaseSlow();
    await expect(pendingLogin).rejects.toThrow(/新的登录或退出操作/);
    const config = JSON.parse(readFileSync(join(root, 'config.json'), 'utf8')) as { token?: string };
    expect(config.token).toBeUndefined();
  });

  it('keeps the later login when an earlier HTTP response arrives last', async () => {
    const earlier = auth.authLogin(serverUrl, 'slow', 'fixture-password');
    await slowStarted;
    await auth.authLogin(serverUrl, 'newer', 'fixture-password');
    releaseSlow();
    await expect(earlier).rejects.toThrow(/新的登录或退出操作/);
    const config = JSON.parse(readFileSync(join(root, 'config.json'), 'utf8')) as { token?: string };
    expect(config.token).toBe('token-newer');
  });

  it('keeps the existing session when the new login fails', async () => {
    const configModule = await import('../config.js');
    configModule.updateConfig({ server: serverUrl, token: 'existing-token' });
    await expect(auth.authLogin(serverUrl, 'invalid', 'fixture-password')).rejects.toThrow('Invalid credentials');
    const config = JSON.parse(readFileSync(join(root, 'config.json'), 'utf8')) as {
      token?: string; auth_login_request_id?: string;
    };
    expect(config.token).toBe('existing-token');
    expect(config.auth_login_request_id).toBeUndefined();
  });
});
