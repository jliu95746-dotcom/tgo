import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { APIError } from '@/services/api';
import { getDeviceReadPolicy, getDeviceScreenGrant, getDeviceScreenPolicy, publishDeviceScreenPolicy, revokeDeviceReadPolicy, revokeDeviceScreenPolicy } from '@/services/deviceControlApi';
import { ownsDevicePolicy, prepareScreenPolicy } from '@/utils/deviceScreenPolicy';
import type { Device, DeviceReadPolicy } from '@/types/deviceControl';
import type { DeviceScreenPolicy, ScreenLocalGrant, ScreenRegion } from '@/types/deviceScreenPolicy';

export interface ScreenPolicyForm { maxActions: number; allowClick: boolean; allowText: boolean; regions: ScreenRegion[] }
interface Owner { session: number; deviceId: string; projectId: string; userId: string }
interface RevokeTarget { kind: 'files' | 'screen'; revision: number }
interface DevicePolicyState {
  owner: Owner | null; file: DeviceReadPolicy | null; screen: DeviceScreenPolicy | null; local: ScreenLocalGrant | null;
  loading: boolean; loaded: boolean; busy: boolean; error: boolean; notice: 'success' | 'revokeSuccess' | null;
  open: (device: Device) => number | null; close: (session: number) => void;
  refresh: () => Promise<void>; publish: (form: ScreenPolicyForm) => Promise<void>;
  revoke: (target: RevokeTarget) => Promise<void>;
}
let nextSession = 0;
let sequence = 0;
let abort: AbortController | null = null;
const empty = { owner: null, file: null, screen: null, local: null, loading: false, loaded: false, busy: false, error: false, notice: null } as const;

export const useDevicePolicyStore = create<DevicePolicyState>((set, get) => {
  const current = (owner: Owner | null): owner is Owner => {
    const user = useAuthStore.getState().user;
    return Boolean(owner && get().owner?.session === owner.session && user?.role === 'admin'
      && user.id === owner.userId && user.project_id === owner.projectId);
  };
  const optional = async <T,>(promise: Promise<T>): Promise<T | null> => {
    try { return await promise; }
    catch (error) { if (error instanceof APIError && error.status === 404) return null; throw error; }
  };
  return {
    ...empty,
    open(device) {
      abort?.abort(); sequence++;
      const user = useAuthStore.getState().user;
      if (!user || user.role !== 'admin' || user.project_id !== device.project_id) { set({ ...empty }); return null; }
      const owner = { session: ++nextSession, deviceId: device.id, projectId: device.project_id, userId: user.id };
      set({ ...empty, owner });
      void get().refresh();
      return owner.session;
    },
    close(session) {
      if (get().owner?.session !== session) return;
      abort?.abort(); sequence++;
      set({ ...empty });
    },
    async refresh() {
      const owner = get().owner;
      if (!current(owner) || get().busy) return;
      abort?.abort(); abort = new AbortController();
      const request = ++sequence;
      const options = { signal: abort.signal };
      set({ loading: true, error: false });
      try {
        const [file, screen, local] = await Promise.all([
          optional(getDeviceReadPolicy(owner.deviceId, options)),
          optional(getDeviceScreenPolicy(owner.deviceId, options)),
          getDeviceScreenGrant(owner.deviceId, options).catch(error => {
            if (error instanceof APIError && [404, 409].includes(error.status)) return null;
            throw error;
          }),
        ]);
        if (!current(owner) || sequence !== request) return;
        if ([file, screen, local].some(value => value && !ownsDevicePolicy(value, owner.deviceId, owner.projectId))) throw new Error('Device policy ownership mismatch');
        set({ file, screen, local, loaded: true });
      } catch {
        if (current(owner) && sequence === request) set({ error: true });
      } finally {
        if (current(owner) && sequence === request) set({ loading: false });
      }
    },
    async publish(form) {
      const state = get(); const owner = state.owner;
      if (!current(owner) || !state.loaded || state.busy || state.loading || state.error) return;
      const payload = prepareScreenPolicy({ ...form, deviceId: owner.deviceId, projectId: owner.projectId,
        local: state.local, file: state.file, screen: state.screen });
      if (!payload) return;
      set({ busy: true, notice: null });
      try {
        const screen = await publishDeviceScreenPolicy(owner.deviceId, payload);
        if (!current(owner)) return;
        if (!ownsDevicePolicy(screen, owner.deviceId, owner.projectId)
          || screen.policy.grant_id !== payload.grant_id || screen.policy.revision !== payload.expected_policy_revision + 1) throw new Error('Device policy response mismatch');
        set({ screen, notice: 'success' });
      } catch { if (current(owner)) set({ error: true }); }
      finally { if (current(owner)) set({ busy: false }); }
      if (current(owner) && !get().error) await get().refresh();
    },
    async revoke(target) {
      const state = get(); const owner = state.owner;
      if (!current(owner) || !state.loaded || state.busy || state.loading || state.error) return;
      const policy = target.kind === 'files' ? state.file : state.screen;
      const revision = target.kind === 'files' ? state.file?.revision : state.screen?.policy.revision;
      if (!policy || policy.status === 'revoked' || revision !== target.revision) return;
      set({ busy: true, notice: null });
      try {
        const result = target.kind === 'files'
          ? await revokeDeviceReadPolicy(owner.deviceId, target.revision)
          : await revokeDeviceScreenPolicy(owner.deviceId, target.revision);
        if (!current(owner)) return;
        if (!ownsDevicePolicy(result, owner.deviceId, owner.projectId) || result.status !== 'revoked') throw new Error('Device revoke response mismatch');
        if (target.kind === 'files') set({ file: result as DeviceReadPolicy });
        else set({ screen: result as DeviceScreenPolicy });
        set({ local: null, notice: 'revokeSuccess' });
      } catch { if (current(owner)) set({ error: true }); }
      finally { if (current(owner)) set({ busy: false }); }
      if (current(owner) && !get().error) await get().refresh();
    },
  };
});
