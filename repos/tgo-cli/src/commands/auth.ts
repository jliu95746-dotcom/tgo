import { Command } from 'commander';
import { TgoClient } from '../client.js';
import {
  beginAuthLogin, cancelAuthLogin, clearAuthSession, completeAuthLogin,
  resolveOutput, resolveServer,
} from '../config.js';
import { printError, printResult } from '../output.js';

export function registerAuthCommands(parent: Command): void {
  const auth = parent.command('auth').description('Authentication commands');

  auth
    .command('login')
    .description('Login and save token')
    .requiredOption('-u, --user <username>', 'Username or email')
    .requiredOption('-p, --pass <password>', 'Password')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const server = resolveServer(globals.server);
        if (!server) {
          throw new Error('Server URL required. Use --server or TGO_SERVER env var.');
        }
        printResult(await authLogin(server, opts.user, opts.pass), format);
      } catch (err) {
        printError(err, format);
      }
    });

  auth
    .command('logout')
    .description('Clear saved token')
    .action((_opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try { printResult(authLogout(), format); }
      catch (error) { printError(error, format); }
    });

  auth
    .command('whoami')
    .description('Show current login info')
    .action(async (_opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = new TgoClient({
          server: resolveServer(globals.server),
          token: globals.token,
        });
        const result = await client.get('/v1/staff/me');
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });
}

// For MCP: standalone action functions
export async function authLogin(server: string, username: string, password: string): Promise<unknown> {
  const requestId = beginAuthLogin();
  try {
    const client = new TgoClient({ server });
    const result = await client.postForm<{
      access_token: string;
      token_type: string;
      staff: Record<string, unknown>;
    }>('/v1/staff/login', {
      username,
      password,
      grant_type: 'password',
    });
    if (!result.access_token || typeof result.access_token !== 'string') {
      throw new Error('登录响应缺少访问令牌');
    }
    if (!completeAuthLogin(requestId, server, result.access_token)) {
      throw new Error('本次登录已被新的登录或退出操作取代，请重试');
    }
    return { success: true, message: 'Logged in successfully', staff: result.staff };
  } catch (error) {
    cancelAuthLogin(requestId);
    throw error;
  }
}

export async function authWhoami(clientOpts?: { server?: string; token?: string }): Promise<unknown> {
  const client = new TgoClient(clientOpts);
  return client.get('/v1/staff/me');
}

export function authLogout(): unknown {
  clearAuthSession();
  return { success: true, message: 'Logged out' };
}
