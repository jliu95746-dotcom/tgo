import { Command } from 'commander';
import { hostname, platform, release, type, arch, userInfo } from 'node:os';
import { TgoWidgetClient } from '../client.js';
import { resolveOutput, resolveServer, resolveApiKey } from '../config.js';
import { beginVisitorInitialization, cancelVisitorInitialization, finishVisitorInitialization } from '../sessionLifecycle.js';
import { printError, printResult } from '../output.js';

interface RegisterResponse {
  id: string;
  platform_open_id: string;
  channel_id: string;
  channel_type: number;
  im_token: string;
  visitor_access_token: string;
  visitor_end_token?: string;
  business_protected?: boolean;
  business_access_expires_at?: string | null;
  platform_id: string;
  project_id?: string;
  created_at?: string;
  updated_at?: string;
}

export function registerInitCommand(parent: Command): void {
  parent
    .command('init')
    .description('Register as visitor and save configuration')
    .option('--name <name>', 'Visitor name')
    .option('--email <email>', 'Visitor email')
    .option('--phone <phone>', 'Visitor phone')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.opts();
      const format = resolveOutput(globals.output);
      const apiKey = resolveApiKey(globals.apiKey);
      if (!apiKey) {
        printError(new Error('API key required. Use: tgo-widget -k <key> init --server <url>'), format);
        return;
      }
      try {
        const result = await initVisitor({
          server: globals.server,
          apiKey,
          name: opts.name,
          email: opts.email,
          phone: opts.phone,
        });
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });
}

export async function initVisitor(params: {
  server?: string;
  apiKey: string;
  name?: string;
  email?: string;
  phone?: string;
}): Promise<unknown> {
  const server = params.server || resolveServer();
  if (!server) throw new Error('No server specified. Use --server <url> or set TGO_WIDGET_SERVER');

  const client = new TgoWidgetClient({ server, apiKey: params.apiKey });
  const requestId = beginVisitorInitialization();

  const body: Record<string, unknown> = {
    platform_api_key: params.apiKey,
  };
  if (params.name) body.name = params.name;
  if (params.email) body.email = params.email;
  if (params.phone) body.phone_number = params.phone;

  // Collect real system info
  const osName = `${type()} ${release()} (${arch()})`;
  body.system_info = {
    browser: `Node.js ${process.version}`,
    operating_system: osName,
    source_detail: `tgo-widget-cli/0.1.0 on ${hostname()}`,
  };
  body.source = 'tgo-widget-cli';
  body.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  body.language = Intl.DateTimeFormat().resolvedOptions().locale || process.env.LANG?.split('.')[0];

  try {
    const data = await client.postPublic<RegisterResponse>('/v1/visitors/register', body);
    if (!data.id || !data.channel_id || !data.im_token || typeof data.visitor_access_token !== 'string'
        || !data.visitor_access_token || data.visitor_access_token.length > 256) {
      throw new Error('访客注册响应缺少访问凭据，请更新服务器后重新初始化');
    }
    if (data.visitor_end_token !== undefined && (typeof data.visitor_end_token !== 'string' || !data.visitor_end_token || data.visitor_end_token.length > 8192)) {
      throw new Error('访客注册响应中的退出凭据无效');
    }

    // Commit only if no newer init or reset superseded this network request.
    const lifecycle = await finishVisitorInitialization(requestId, {
      server,
      api_key: params.apiKey,
      visitor_id: data.id,
      platform_open_id: data.platform_open_id,
      channel_id: data.channel_id,
      channel_type: data.channel_type,
      im_token: data.im_token,
      visitor_access_token: data.visitor_access_token,
      visitor_end_token: data.visitor_end_token,
      business_protected: data.business_protected,
      business_access_expires_at: data.business_access_expires_at,
      project_id: data.project_id,
      platform_id: data.platform_id,
    });

    return {
      status: 'initialized',
      visitor_id: data.id,
      platform_open_id: data.platform_open_id,
      channel_id: data.channel_id,
      channel_type: data.channel_type,
      platform_id: data.platform_id,
      server,
      ...lifecycle,
    };
  } catch (error) {
    cancelVisitorInitialization(requestId);
    throw error;
  }
}
