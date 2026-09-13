export interface MCPHeaderInput {
  key: string;
  value: string;
}

export function readMcpHeaders(value: unknown): MCPHeaderInput[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value).map(([key, item]) => ({ key, value: typeof item === 'string' ? item : '' }));
}

export function serializeMcpHeaders(rows: MCPHeaderInput[] = []): Record<string, string> {
  const result: Record<string, string> = {};
  const seen = new Set<string>();
  for (const { key: rawKey, value } of rows) {
    const key = rawKey.trim();
    if (!key && !value) continue;
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key) || /[^\x20-\x7e]/.test(value) || seen.has(key.toLowerCase())) {
      throw new Error('INVALID_MCP_HEADERS');
    }
    seen.add(key.toLowerCase());
    Object.defineProperty(result, key, { value, enumerable: true, configurable: true, writable: true });
  }
  return result;
}

export function redactMcpHeaderValues(message: string, headers: unknown): string {
  let safe = String(message);
  const values = readMcpHeaders(headers).flatMap(row => [row.value, row.value.replace(/^(Bearer|Basic)\s+/i, '')]);
  for (const value of values.filter(Boolean).sort((a, b) => b.length - a.length)) {
    safe = safe.split(value).join('[redacted]');
  }
  return safe;
}
