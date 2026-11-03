import { readSync } from 'node:fs';

export type BusinessCustomerSessionInput = Readonly<{ connection_id: string; customer_session: string }>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FAILURE = '客户业务身份暂不可确认，请重新验证后发送或转人工。';

export function normalizeCustomerSessions(value: unknown): readonly BusinessCustomerSessionInput[] {
  if (!Array.isArray(value) || value.length > 20) throw new Error(FAILURE);
  const seen = new Set<string>();
  return Object.freeze(value.map((entry: unknown) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error(FAILURE);
    const item = entry as Record<string, unknown>;
    if (Object.keys(item).length !== 2 || Object.keys(item).some(key => !['connection_id', 'customer_session'].includes(key))
      || typeof item.connection_id !== 'string' || !UUID.test(item.connection_id)
      || typeof item.customer_session !== 'string' || !item.customer_session.trim() || item.customer_session.length > 8192) throw new Error(FAILURE);
    const id = item.connection_id.toLowerCase();
    if (seen.has(id)) throw new Error(FAILURE);
    seen.add(id);
    return Object.freeze({ connection_id: id, customer_session: item.customer_session });
  }));
}

/** Opt-in input is bounded and never saved to CLI config or command arguments. */
export function readCustomerSessionsFromStdin(): readonly BusinessCustomerSessionInput[] {
  const chunks: Buffer[] = [];
  let size = 0;
  const buffer = Buffer.alloc(8192);
  try {
    for (;;) {
      const count = readSync(0, buffer, 0, buffer.length, null);
      if (!count) break;
      size += count;
      if (size > 192 * 1024) throw new Error(FAILURE);
      chunks.push(Buffer.from(buffer.subarray(0, count)));
    }
    return normalizeCustomerSessions(JSON.parse(Buffer.concat(chunks).toString('utf8')));
  } catch { throw new Error(FAILURE); }
}

export function ensureNoCustomerSessionEcho(value: unknown, sessions: readonly BusinessCustomerSessionInput[]): void {
  const text = JSON.stringify(value) || '';
  if (sessions.some(session => text.includes(session.customer_session))) throw new Error(FAILURE);
}
