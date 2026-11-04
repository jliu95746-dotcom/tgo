/** Persist only opaque request identifiers; credentials and test inputs stay in memory. */
import type { BusinessCapability } from '@/types/businessConnections';

export interface ConnectionRecoveryPointer { connectionId: string; requestId: string; operation: BusinessCapability }
export interface ConnectionRecovery { creation: string | null; tests: ConnectionRecoveryPointer[] }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const key = (scope: string) => `tgo:business-connection-recovery:v1:${scope}`;
export function readConnectionRecovery(scope: string): ConnectionRecovery {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key(scope)) || '{}');
    if (!value || typeof value !== 'object') return { creation: null, tests: [] };
    const raw = value as Record<string, unknown>;
    const tests: ConnectionRecoveryPointer[] = [];
    if (Array.isArray(raw.tests)) for (const item of raw.tests.slice(0, 20)) {
      if (!item || typeof item !== 'object') continue;
      const pointer = item as Record<string, unknown>;
      if (typeof pointer.connectionId === 'string' && uuid.test(pointer.connectionId) &&
          typeof pointer.requestId === 'string' && uuid.test(pointer.requestId) &&
          (pointer.operation === 'order_query' || pointer.operation === 'logistics_query' ||
           pointer.operation === 'customer_profile_query' || pointer.operation === 'membership_query')) {
        tests.push({ connectionId: pointer.connectionId, requestId: pointer.requestId, operation: pointer.operation });
      }
    }
    return { creation: typeof raw.creation === 'string' && uuid.test(raw.creation) ? raw.creation : null, tests };
  } catch { return { creation: null, tests: [] }; }
}
export function saveConnectionRecovery(scope: string, value: ConnectionRecovery): boolean {
  try {
    const safe = { creation: value.creation, tests: value.tests.map(({ connectionId, requestId, operation }) => ({ connectionId, requestId, operation })).slice(0, 20) };
    if (!safe.creation && !safe.tests.length) localStorage.removeItem(key(scope));
    else localStorage.setItem(key(scope), JSON.stringify(safe));
    return true;
  } catch { return false; }
}
