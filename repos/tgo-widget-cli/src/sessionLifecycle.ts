import { randomUUID } from 'node:crypto';
import { changeConfig, loadConfig, type PendingVisitorEnd, type TgoWidgetConfig } from './config.js';
import { TgoWidgetClient, type VisitorEndOutcome } from './client.js';

function pending(config: TgoWidgetConfig): PendingVisitorEnd[] {
  if (config.pending_visitor_ends === undefined) return [];
  if (!Array.isArray(config.pending_visitor_ends) || config.pending_visitor_ends.length > 20) {
    throw new Error('退出重试记录损坏，请保留配置文件并人工核对');
  }
  return config.pending_visitor_ends.map(item => {
    if (!item || typeof item.server !== 'string' || !item.server
      || typeof item.api_key !== 'string' || !item.api_key
      || typeof item.visitor_id !== 'string' || !item.visitor_id
      || typeof item.visitor_end_token !== 'string' || !item.visitor_end_token
      || item.visitor_end_token.length > 8192) {
      throw new Error('退出重试记录损坏，请保留配置文件并人工核对');
    }
    return { server: item.server, api_key: item.api_key,
      visitor_id: item.visitor_id, visitor_end_token: item.visitor_end_token };
  });
}

function currentEnd(config: TgoWidgetConfig): PendingVisitorEnd | undefined {
  return config.server && config.api_key && config.visitor_id
    && typeof config.visitor_end_token === 'string' && config.visitor_end_token && config.visitor_end_token.length <= 8192
    ? { server: config.server, api_key: config.api_key, visitor_id: config.visitor_id, visitor_end_token: config.visitor_end_token }
    : undefined;
}

function sameEnd(left: PendingVisitorEnd, right: PendingVisitorEnd): boolean {
  return left.server === right.server && left.api_key === right.api_key
    && left.visitor_id === right.visitor_id && left.visitor_end_token === right.visitor_end_token;
}

function addPending(config: TgoWidgetConfig, item: PendingVisitorEnd): void {
  const records = pending(config);
  if (records.some(value => sameEnd(value, item))) return;
  if (records.length >= 20) throw new Error('旧会话退出尚待确认，请先重试退出。');
  config.pending_visitor_ends = [...records, item];
}

async function submit(item: PendingVisitorEnd): Promise<VisitorEndOutcome> {
  // Use the original authority, never global flags for another project/server.
  const client = new TgoWidgetClient({server:item.server,apiKey:item.api_key});
  const receipt = await client.endVisitorAccess({visitorId:item.visitor_id,visitorEndToken:item.visitor_end_token});
  if (receipt.accessEnded && receipt.upstreamStopStatus==='confirmed' && receipt.imDisconnectStatus==='requested') {
    changeConfig(config => {
      config.pending_visitor_ends = pending(config).filter(value => !sameEnd(value, item));
    });
  }
  return receipt;
}

export async function endCurrentVisitorSession(): Promise<VisitorEndOutcome> {
  const item = changeConfig(config => {
    const receipt = currentEnd(config);
    if (receipt) addPending(config, receipt);
    // Invalidate in-flight registration and remove read/IM authority before the network call.
    const keep = { server: config.server, api_key: config.api_key, output: config.output,
      pending_visitor_ends: config.pending_visitor_ends, init_request_id: randomUUID() };
    for (const key of Object.keys(config) as (keyof TgoWidgetConfig)[]) delete config[key];
    Object.assign(config, keep);
    return receipt;
  });
  if (!item) return {accessEnded:false,upstreamStopStatus:'pending',imDisconnectStatus:'pending'};
  return submit(item);
}

export function beginVisitorInitialization(): string {
  const requestId = randomUUID();
  changeConfig(config => {
    if (pending(config).length >= 20) throw new Error('旧会话退出尚待确认，请先重试退出。');
    config.init_request_id = requestId;
  });
  return requestId;
}

export function cancelVisitorInitialization(requestId: string): void {
  changeConfig(config => {
    if (config.init_request_id === requestId) delete config.init_request_id;
  });
}

export async function finishVisitorInitialization(requestId: string, next: TgoWidgetConfig): Promise<{ previous_end_pending: boolean }> {
  const previous = changeConfig(config => {
    if (config.init_request_id !== requestId) return { valid: false, oldEnd: undefined as PendingVisitorEnd | undefined };
    const replaced = !!config.visitor_id && (config.visitor_id !== next.visitor_id
      || config.server !== next.server || config.api_key !== next.api_key);
    const oldEnd = replaced ? currentEnd(config) : undefined;
    if (oldEnd) addPending(config, oldEnd);
    if (replaced) {
      const keep = { server: config.server, api_key: config.api_key, output: config.output,
        pending_visitor_ends: config.pending_visitor_ends, init_request_id: requestId };
      for (const key of Object.keys(config) as (keyof TgoWidgetConfig)[]) delete config[key];
      Object.assign(config, keep);
    }
    return { valid: true, oldEnd };
  });
  let previousEndPending = false;
  if (previous.oldEnd) {
    try {
      const outcome = await submit(previous.oldEnd);
      previousEndPending = !outcome.accessEnded || outcome.upstreamStopStatus !== 'confirmed'
        || outcome.imDisconnectStatus !== 'requested';
    } catch {
      previousEndPending = true;
    }
  }
  const accepted = changeConfig(config => {
    if (!previous.valid || config.init_request_id !== requestId) return false;
    const output = config.output;
    const pendingVisitorEnds = config.pending_visitor_ends;
    for (const key of Object.keys(config) as (keyof TgoWidgetConfig)[]) delete config[key];
    config.output = output;
    config.pending_visitor_ends = pendingVisitorEnds;
    Object.assign(config, next);
    for (const key of Object.keys(config) as (keyof TgoWidgetConfig)[]) {
      if (config[key] == null) delete config[key];
    }
    return true;
  });
  if (!accepted) {
    const abandoned = currentEnd(next);
    if (abandoned) {
      const shouldRetire = changeConfig(config => {
        // A server may reuse a visitor ID for two registrations; never end the active one.
        if (config.server === abandoned.server && config.api_key === abandoned.api_key
          && config.visitor_id === abandoned.visitor_id) return false;
        addPending(config, abandoned);
        return true;
      });
      if (shouldRetire) {
        try { await submit(abandoned); } catch { /* Retry record remains. */ }
      }
    }
    throw new Error('本次初始化已被较新的登录或退出操作取代，请重试');
  }
  return { previous_end_pending: previousEndPending };
}

export async function retryPendingVisitorEnds(): Promise<VisitorEndOutcome[]> {
  const results: VisitorEndOutcome[] = [];
  for (const item of pending(loadConfig())) results.push(await submit(item));
  return results;
}
