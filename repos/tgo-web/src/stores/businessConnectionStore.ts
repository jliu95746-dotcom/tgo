import { create } from 'zustand';
import { BusinessConnectionsApi } from '@/services/businessConnectionsApi';
import { APIError } from '@/services/api';
import type { BusinessConnection, BusinessConnectionCreate, BusinessConnectionUpdate, BusinessConnectionTestRequest, BusinessConnectionTestReport, BusinessCapability } from '@/types/businessConnections';
import { readConnectionRecovery, saveConnectionRecovery } from '@/utils/businessConnectionRecovery';
import { useAuthStore } from './authStore';
import { useScenarioResourceStore } from './scenarioResourceStore';

export type ConnectionError = 'network' | 'forbidden' | 'missing' | 'conflict' | 'invalid' | 'quota' | 'unavailable' | 'invalidResponse';
interface PendingTest { requestId: string; operation: BusinessCapability; body: BusinessConnectionTestRequest | null; unknown: boolean }
interface ConnectionState {
  connections: BusinessConnection[]; selectedId: string | null; reports: BusinessConnectionTestReport[];
  loading: boolean; historyLoading: boolean; busy: boolean; submitting: boolean; cancelling: boolean;
  hasMore: boolean; error: ConnectionError | null; storageAvailable: boolean;
  createUnknown: boolean; creationId: string | null; creationBody: BusinessConnectionCreate | null;
  pendingTests: Record<string, PendingTest>;
  load: (more?: boolean) => Promise<void>; select: (id: string) => Promise<void>;
  create: (body: Omit<BusinessConnectionCreate, 'request_id'>) => Promise<boolean>;
  recoverCreate: () => Promise<void>; retryCreate: () => Promise<boolean>;
  forgetCreation: () => void;
  update: (id: string, body: BusinessConnectionUpdate) => Promise<boolean>;
  startTest: (operation: BusinessCapability, order: string, customer: string, expectedRevision?: number) => Promise<void>;
  recoverTest: () => Promise<void>; retryTest: () => Promise<void>; cancelTest: () => Promise<void>;
  refreshHistory: () => Promise<void>; clearError: () => void;
}
let scopeVersion = 0, listVersion = 0, selectionVersion = 0, testVersion = 0;
let testController: AbortController | null = null;
const metadataVersions = new Map<string,number>();
const scope = () => { const user = useAuthStore.getState().user; return user?.role === 'admin' && user.id && user.project_id ? `${user.project_id}:${user.id}` : null; };
const classify = (error: unknown): ConnectionError => {
  if (error instanceof Error && error.message === 'invalidResponse') return 'invalidResponse';
  if (!(error instanceof APIError)) return 'network';
  if ([401,403].includes(error.status)) return 'forbidden';
  if (error.status === 404) return 'missing';
  if (error.status === 409) return 'conflict';
  if ([400,422].includes(error.status)) return 'invalid';
  if ([402,429].includes(error.status)) return 'quota';
  return error.status === 503 ? 'unavailable' : 'network';
};
const empty = { connections: [], selectedId: null, reports: [], loading: false, historyLoading: false, busy: false,
  submitting: false, cancelling: false, hasMore: false, error: null, storageAvailable: true,
  createUnknown: false, creationId: null, creationBody: null, pendingTests: {} };

export const useBusinessConnectionStore = create<ConnectionState>((set, get) => {
  const active = (version: number, identity: string | null) => identity !== null && version === scopeVersion && identity === scope();
  const persist = () => {
    const identity = scope(); if (!identity) return;
    const state = get();
    const storageAvailable = saveConnectionRecovery(identity, { creation: state.creationId,
      tests: Object.entries(state.pendingTests).map(([connectionId, value]) => ({ connectionId, requestId: value.requestId, operation: value.operation })) });
    set({ storageAvailable });
  };
  const restore = () => {
    const identity = scope(); if (!identity) return;
    const saved = readConnectionRecovery(identity);
    const tests = { ...get().pendingTests };
    for (const value of saved.tests) if (!tests[value.connectionId]) tests[value.connectionId] = { requestId: value.requestId, operation: value.operation, body: null, unknown: true };
    set({ pendingTests: tests, ...(saved.creation && !get().creationId ? { creationId: saved.creation, creationBody: null, createUnknown: true } : {}) });
  };
  const checkConnection = (value: BusinessConnection, expected?: string) => {
    if (!value || value.project_id !== useAuthStore.getState().user?.project_id || (expected && value.id !== expected) ||
        !['http','demo'].includes(value.mode) || !['passed','not_tested','unavailable'].includes(value.test_status) ||
        typeof value.revision !== 'number' || typeof value.enabled !== 'boolean' || 'credentials' in value || 'credential_ciphertext' in value) throw new Error('invalidResponse');
  };
  const acceptConnection = (value: BusinessConnection, expected?: string) => {
    checkConnection(value, expected);
    const previous = get().connections.find(row => row.id === value.id);
    if (previous && previous.revision > value.revision) return;
    set({ connections: [value, ...get().connections.filter(row => row.id !== value.id)] });
  };
  const failed = (error: unknown) => {
    const kind = classify(error);
    if (kind === 'forbidden') {
      scopeVersion += 1; testController?.abort(); set({ ...empty, error: kind });
    } else set({ error: kind });
  };
  const acceptReport = (value: BusinessConnectionTestReport, id: string, requestId?: string) => {
    if (!value || value.connection_id !== id || (requestId && value.request_id !== requestId) || value.scope !== 'business_connection_test' ||
      value.can_enable !== false || !['running','completed','failed','cancelled'].includes(value.status) ||
      !['order_query','logistics_query','customer_profile_query','membership_query'].includes(value.operation) || (value.mode === 'demo' && value.outcome === 'passed') ||
      (value.applicability?.status === 'current' && (value.mode !== 'http' || value.outcome !== 'passed'))) throw new Error('invalidResponse');
    const previous = get().reports.find(row => row.id === value.id);
    let report = value;
    if (previous && previous.status !== 'running') {
      report = { ...previous, applicability: value.applicability ?? previous.applicability };
      if (previous.applicability?.checked_at > value.applicability?.checked_at) report = previous;
    }
    set({ reports: [report, ...get().reports.filter(row => row.id !== report.id)].sort((left,right) => right.created_at.localeCompare(left.created_at)) });
    if (report.status !== 'running' && get().pendingTests[id]?.requestId === report.request_id) {
      const pendingTests = { ...get().pendingTests }; delete pendingTests[id]; set({ pendingTests }); persist();
    }
  };
  const metadata = async (id: string) => {
    const version = scopeVersion, identity = scope();
    const metadataVersion=(metadataVersions.get(id)??0)+1, generation=testVersion; metadataVersions.set(id,metadataVersion);
    try { const value = await BusinessConnectionsApi.get(id); if (active(version, identity)&&metadataVersions.get(id)===metadataVersion&&generation===testVersion) acceptConnection(value, id); }
    catch (error) { if (active(version, identity)) failed(error); }
    void useScenarioResourceStore.getState().load(true);
  };
  const submitCreation = async (body: BusinessConnectionCreate): Promise<boolean> => {
    if (!scope() || get().busy) return false;
    const version = scopeVersion, identity = scope(); listVersion += 1;
    set({ busy: true, loading: false, error: null, creationId: body.request_id, creationBody: body, createUnknown: false }); persist();
    try {
      const value = await BusinessConnectionsApi.create(body);
      if (!active(version, identity)) return false;
      acceptConnection(value); set({ creationId: null, creationBody: null, createUnknown: false }); persist(); return true;
    } catch (error) {
      if (active(version, identity)) {
        const rejected = error instanceof APIError && [400,401,403,404,422].includes(error.status);
        set({ createUnknown: !rejected, ...(rejected ? { creationId: null, creationBody: null } : {}) }); persist(); failed(error);
      }
      return false;
    } finally { if (active(version, identity)) set({ busy: false }); }
  };
  const submitTest = async (id: string, body: BusinessConnectionTestRequest) => {
    const version = scopeVersion, identity = scope(), selection = selectionVersion, requestVersion = ++testVersion;
    const controller = new AbortController(); testController = controller;
    set({ submitting: true, error: null, pendingTests: { ...get().pendingTests, [id]: { requestId: body.request_id, operation: body.operation, body, unknown: false } } }); persist();
    try {
      const value = await BusinessConnectionsApi.test(id, body, controller.signal);
      if (active(version, identity) && selection === selectionVersion && requestVersion === testVersion && !controller.signal.aborted) {
        acceptReport(value, id, body.request_id); void metadata(id);
      }
    } catch (error) {
      if (active(version, identity) && selection === selectionVersion && requestVersion === testVersion && !controller.signal.aborted) {
        const pending = get().pendingTests[id];
        if (pending) set({ pendingTests: { ...get().pendingTests, [id]: { ...pending, unknown: true } } });
        failed(error); persist();
      }
    } finally { if (active(version, identity) && requestVersion === testVersion) set({ submitting: false }); }
  };
  return {
    ...empty,
    clearError: () => set({ error: null }),
    forgetCreation: () => { if (!get().busy) { set({ creationId: null, creationBody: null, createUnknown: false, error: null }); persist(); } },
    load: async (more = false) => {
      if (!scope() || get().loading) return; restore();
      const version = scopeVersion, identity = scope(), request = ++listVersion;
      const offset = more ? get().connections.length : 0;
      set({ loading: true, error: null });
      try {
        const values = await BusinessConnectionsApi.list(offset);
        if (!active(version, identity) || request !== listVersion) return;
        for (const value of values) checkConnection(value);
        set({ connections: more ? [...get().connections, ...values.filter(value => !get().connections.some(row => row.id === value.id))] : values, hasMore: values.length === 100 });
      } catch (error) { if (active(version, identity) && request === listVersion) failed(error); }
      finally { if (active(version, identity) && request === listVersion) set({ loading: false }); }
    },
    select: async id => {
      if (!scope() || !get().connections.some(row => row.id === id)) return;
      selectionVersion += 1; testVersion += 1; testController?.abort();
      set({ selectedId: id, reports: [], submitting: false, cancelling: false, error: null }); restore();
      await get().refreshHistory();
    },
    refreshHistory: async () => {
      const id = get().selectedId; if (!scope() || !id) return;
      const version = scopeVersion, identity = scope(), selection = selectionVersion;
      set({ historyLoading: true, error: null });
      try {
        const reports = await BusinessConnectionsApi.tests(id);
        if (!active(version, identity) || selection !== selectionVersion) return;
        for (const report of reports) acceptReport(report, id);
      } catch (error) { if (active(version, identity) && selection === selectionVersion) failed(error); }
      finally { if (active(version, identity) && selection === selectionVersion) set({ historyLoading: false }); }
    },
    create: body => get().creationId ? Promise.resolve(false) : submitCreation({ ...body, request_id: crypto.randomUUID() }),
    retryCreate: () => get().creationBody ? submitCreation(get().creationBody!) : Promise.resolve(false),
    recoverCreate: async () => {
      restore(); const id = get().creationId; if (!id || !scope() || get().busy) return;
      const version = scopeVersion, identity = scope(); set({ busy: true, error: null });
      try {
        const value = await BusinessConnectionsApi.created(id);
        if (active(version, identity)) { acceptConnection(value); set({ creationId: null, creationBody: null, createUnknown: false }); persist(); }
      } catch (error) { if (active(version, identity)) failed(error); }
      finally { if (active(version, identity)) set({ busy: false }); }
    },
    update: async (id, body) => {
      if (!scope() || get().busy) return false;
      const version = scopeVersion, identity = scope(); listVersion += 1; set({ busy: true, loading: false, error: null });
      try {
        const value = await BusinessConnectionsApi.update(id, body);
        if (!active(version, identity)) return false;
        acceptConnection(value, id); void useScenarioResourceStore.getState().load(true); return true;
      } catch (error) { if (active(version, identity)) failed(error); return false; }
      finally { if (active(version, identity)) set({ busy: false }); }
    },
    startTest: async (operation, order, customer, expectedRevision) => {
      const id = get().selectedId, connection = get().connections.find(row => row.id === id);
      if (!scope() || !id || !connection || get().submitting || get().busy || get().pendingTests[id]) return;
      if (expectedRevision !== undefined && expectedRevision !== connection.revision) { set({ error: 'conflict' }); return; }
      await submitTest(id, { request_id: crypto.randomUUID(), expected_revision: connection.revision, operation,
        ...(operation === 'order_query' || operation === 'logistics_query' ? { order_no: order } : {}), customer_id: customer });
    },
    recoverTest: async () => {
      const id = get().selectedId, pending = id ? get().pendingTests[id] : null;
      if (!scope() || !id || !pending || get().cancelling) return;
      const version = scopeVersion, identity = scope(), selection = selectionVersion;
      try { const value = await BusinessConnectionsApi.testReport(id, pending.requestId);
        if (active(version, identity) && selection === selectionVersion) { acceptReport(value, id, pending.requestId); void metadata(id); }
      } catch (error) { if (active(version, identity) && selection === selectionVersion) failed(error); }
    },
    retryTest: async () => {
      const id = get().selectedId, pending = id ? get().pendingTests[id] : null;
      if (!scope() || !id || !pending || get().submitting || get().cancelling) return;
      if (pending.body) await submitTest(id, pending.body); else await get().recoverTest();
    },
    cancelTest: async () => {
      const id = get().selectedId, pending = id ? get().pendingTests[id] : null;
      if (!scope() || !id || !pending || get().cancelling) return;
      const version = scopeVersion, identity = scope(), selection = selectionVersion;
      set({ cancelling: true, error: null });
      try { const value = await BusinessConnectionsApi.cancelTest(id, pending.requestId);
        if (active(version, identity) && selection === selectionVersion) {
          acceptReport(value, id, pending.requestId); testVersion += 1; testController?.abort(); set({ submitting: false }); void metadata(id);
        }
      } catch (error) { if (active(version, identity) && selection === selectionVersion) failed(error); }
      finally { if (active(version, identity) && selection === selectionVersion) set({ cancelling: false }); }
    },
  };
});
useAuthStore.subscribe((state, previous) => {
  if (state.user?.id !== previous.user?.id || state.user?.project_id !== previous.user?.project_id || state.user?.role !== previous.user?.role) {
    scopeVersion += 1; listVersion += 1; selectionVersion += 1; testVersion += 1; testController?.abort();
    useBusinessConnectionStore.setState({ ...empty });
  }
});
