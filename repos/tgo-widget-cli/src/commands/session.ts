import { Command } from 'commander';
import { resolveOutput } from '../config.js';
import { printError, printResult } from '../output.js';
import { endCurrentVisitorSession, retryPendingVisitorEnds } from '../sessionLifecycle.js';

export function registerSessionCommands(parent: Command): void {
  const group = parent.command('session').description('访客会话与退出确认');
  group.command('end').description('结束当前访客访问并清理读取凭据').action(async () => {
    const format = resolveOutput(parent.opts().output);
    try { printResult(await endCurrentVisitorSession(),format); }
    catch { printError(new Error('访客退出尚待确认，请检查配置存储并重试。'),format); }
  });
  group.command('retry-end').description('重查原退出请求，不恢复旧会话').action(async () => {
    const format = resolveOutput(parent.opts().output);
    try { printResult(await retryPendingVisitorEnds(),format); }
    catch { printError(new Error('原退出请求尚待确认，请稍后重试。'),format); }
  });
}
