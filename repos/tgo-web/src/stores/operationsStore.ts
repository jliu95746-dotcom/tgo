import { create } from 'zustand';
import { APIError } from '../services/api';
import { operationsApi } from '../services/operationsApi';
import type { MigrationPreviewResponse, OperatorLoginRequest, OperatorProfile, OperationsStatus } from '../types/operations';

interface OperationsState {
  status: OperationsStatus | null;
  operator: OperatorProfile | null;
  preview: MigrationPreviewResponse | null;
  loading: boolean;
  error: string | null;
  initialize: () => Promise<void>;
  login: (data: OperatorLoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  loadPreview: (offset?: number) => Promise<void>;
}

const errorMessage = (error: unknown): string => {
  if (!(error instanceof APIError) || error.status === 0) return 'operations.connectionError';
  if (error.status === 404) return 'operations.unavailable';
  return error.message;
};

// Prevent a late response from an earlier login from repopulating a cleared console.
let sessionGeneration = 0;

export const useOperationsStore = create<OperationsState>((set, get) => ({
  status: null, operator: null, preview: null, loading: false, error: null,
  async initialize() {
    if (get().loading) return;
    const generation = ++sessionGeneration;
    set({ loading: true, error: null });
    try {
      const status = await operationsApi.status();
      if (generation !== sessionGeneration) return;
      set({ status });
      if (!status.login_available) {
        operationsApi.clearSession();
        set({ operator: null, preview: null });
      } else if (operationsApi.hasSession()) {
        const operator = await operationsApi.me();
        if (generation !== sessionGeneration) return;
        const preview = await operationsApi.preview();
        if (generation === sessionGeneration) set({ operator, preview });
      }
    } catch (error) {
      if (generation === sessionGeneration) set({ error: errorMessage(error) });
    } finally {
      if (generation === sessionGeneration) set({ loading: false });
    }
  },
  async login(data) {
    if (get().loading) return;
    const generation = ++sessionGeneration;
    set({ loading: true, error: null, preview: null });
    try {
      const operator = await operationsApi.login(data);
      if (generation !== sessionGeneration) return;
      set({ operator });
      const preview = await operationsApi.preview();
      if (generation === sessionGeneration) set({ preview });
    } catch (error) {
      if (generation === sessionGeneration) set({ error: errorMessage(error) });
    } finally {
      if (generation === sessionGeneration) set({ loading: false });
    }
  },
  async logout() {
    if (get().loading) return;
    set({ loading: true, error: null });
    try {
      await operationsApi.logout();
      sessionGeneration += 1;
      set({ operator: null, preview: null });
    } catch (error) {
      set({ error: errorMessage(error) });
    } finally { set({ loading: false }); }
  },
  async loadPreview(offset = 0) {
    if (get().loading || !get().operator) return;
    const generation = sessionGeneration;
    set({ loading: true, error: null });
    try {
      const preview = await operationsApi.preview(offset);
      if (generation === sessionGeneration) set({ preview });
    } catch (error) {
      if (generation === sessionGeneration) set({ error: errorMessage(error) });
    } finally {
      if (generation === sessionGeneration) set({ loading: false });
    }
  },
}));

operationsApi.onSessionExpired(() => {
  sessionGeneration += 1;
  useOperationsStore.setState({ operator: null, preview: null, loading: false, error: 'operations.sessionExpired' });
});
