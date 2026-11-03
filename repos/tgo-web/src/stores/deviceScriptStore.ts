import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { APIError } from '@/services/api';
import {
  getDeviceReadPolicy, getDeviceScreenPolicy, getDeviceScriptCatalog,
  getDeviceScriptPolicy, getDeviceScriptStatus, publishDeviceScriptPolicy,
  revokeDeviceScriptPolicy,
} from '@/services/deviceControlApi';
import { ownsScriptScope, prepareScriptPolicy, type ScriptPolicyForm } from '@/utils/deviceScriptPolicy';
import type { Device, DeviceReadPolicy } from '@/types/deviceControl';
import type { DeviceScreenPolicy } from '@/types/deviceScreenPolicy';
import type { ScriptCatalog, ScriptLocalStatusRead, ScriptPolicyRead } from '@/types/deviceScriptPolicy';

interface Owner { session: number; deviceId: string; projectId: string; userId: string }
interface ScriptState {
  owner: Owner | null;
  local: ScriptLocalStatusRead | null;
  catalog: ScriptCatalog | null;
  policy: ScriptPolicyRead | null;
  file: DeviceReadPolicy | null;
  screen: DeviceScreenPolicy | null;
  loading: boolean;
  loaded: boolean;
  busy: boolean;
  error: boolean;
  notice: 'published' | 'revoked' | null;
  open: (device: Device) => number | null;
  close: (session: number) => void;
  refresh: () => Promise<void>;
  publish: (form: ScriptPolicyForm) => Promise<void>;
  revoke: (expectedRevision: number) => Promise<void>;
}

let sessionNumber = 0;
let sequence = 0;
let controller: AbortController | null = null;
const empty = {
  owner: null, local: null, catalog: null, policy: null, file: null, screen: null,
  loading: false, loaded: false, busy: false, error: false, notice: null,
} as const;

export const useDeviceScriptStore = create<ScriptState>((set, get) => {
  const current = (owner: Owner | null): owner is Owner => {
    const user = useAuthStore.getState().user;
    return Boolean(owner && get().owner?.session === owner.session && user?.role === 'admin'
      && user.id === owner.userId && user.project_id === owner.projectId);
  };
  const optional = async <T,>(promise: Promise<T>, unavailable = false): Promise<T | null> => {
    try { return await promise; }
    catch (error) {
      if (error instanceof APIError && (error.status === 404 || unavailable && error.status === 409)) return null;
      throw error;
    }
  };
  return {
    ...empty,
    open(device) {
      controller?.abort(); sequence++;
      const user = useAuthStore.getState().user;
      if (!user || user.role !== 'admin' || user.project_id !== device.project_id) {
        set({ ...empty }); return null;
      }
      const owner = { session: ++sessionNumber, deviceId: device.id, projectId: device.project_id, userId: user.id };
      set({ ...empty, owner });
      void get().refresh();
      return owner.session;
    },
    close(session) {
      if (get().owner?.session !== session) return;
      controller?.abort(); sequence++;
      set({ ...empty });
    },
    async refresh() {
      const owner = get().owner;
      if (!current(owner) || get().busy) return;
      controller?.abort(); controller = new AbortController();
      const request = ++sequence;
      const options = { signal: controller.signal };
      set({ loading: true, loaded: false, error: false, local: null, catalog: null, policy: null, file: null, screen: null });
      try {
        const [local, catalog, policy, file, screen] = await Promise.all([
          optional(getDeviceScriptStatus(owner.deviceId, options), true),
          optional(getDeviceScriptCatalog(owner.deviceId, options), true),
          optional(getDeviceScriptPolicy(owner.deviceId, options)),
          optional(getDeviceReadPolicy(owner.deviceId, options)),
          optional(getDeviceScreenPolicy(owner.deviceId, options)),
        ]);
        if (!current(owner) || request !== sequence) return;
        if ([local, policy, file, screen].some(value => value && !ownsScriptScope(value, owner.deviceId, owner.projectId))) {
          throw new Error('Device script metadata scope mismatch');
        }
        set({ local, catalog, policy, file, screen, loaded: true });
      } catch {
        if (current(owner) && request === sequence) set({ error: true });
      } finally {
        if (current(owner) && request === sequence) set({ loading: false });
      }
    },
    async publish(form) {
      const state = get(); const owner = state.owner;
      if (!current(owner) || !state.loaded || state.loading || state.busy || state.error) return;
      const payload = prepareScriptPolicy({ ...form, ...state, deviceId: owner.deviceId, projectId: owner.projectId });
      if (!payload) return;
      set({ busy: true, notice: null });
      try {
        const policy = await publishDeviceScriptPolicy(owner.deviceId, payload);
        if (!current(owner)) return;
        if (!ownsScriptScope(policy, owner.deviceId, owner.projectId)
          || policy.policy.revision !== payload.expected_policy_revision + 1
          || policy.policy.script_id !== payload.script_id
          || policy.policy.grant_digest !== payload.grant_digest
          || policy.policy.timeout_seconds !== payload.timeout_seconds
          || policy.policy.max_runs !== payload.max_runs
          || policy.policy.max_output_bytes !== payload.max_output_bytes
          || policy.status === 'revoked' || policy.verified !== false) throw new Error('Device script publication mismatch');
        set({ policy, notice: 'published' });
      } catch { if (current(owner)) set({ error: true }); }
      finally { if (current(owner)) set({ busy: false }); }
      if (current(owner) && !get().error) await get().refresh();
    },
    async revoke(expectedRevision) {
      const state = get(); const owner = state.owner;
      if (!current(owner) || !state.loaded || state.loading || state.busy || state.error
        || !state.policy || state.policy.status === 'revoked' || state.policy.policy.revision !== expectedRevision) return;
      set({ busy: true, notice: null });
      try {
        const policy = await revokeDeviceScriptPolicy(owner.deviceId, expectedRevision);
        if (!current(owner)) return;
        if (!ownsScriptScope(policy, owner.deviceId, owner.projectId) || policy.status !== 'revoked'
          || policy.policy.revision !== expectedRevision + 1) throw new Error('Device script revocation mismatch');
        set({ policy, local: null, notice: 'revoked' });
      } catch { if (current(owner)) set({ error: true }); }
      finally { if (current(owner)) set({ busy: false }); }
      if (current(owner) && !get().error) await get().refresh();
    },
  };
});

useAuthStore.subscribe(state => {
  const owner = useDeviceScriptStore.getState().owner;
  if (owner && (state.user?.id !== owner.userId || state.user?.project_id !== owner.projectId || state.user?.role !== 'admin')) {
    useDeviceScriptStore.getState().close(owner.session);
  }
});
