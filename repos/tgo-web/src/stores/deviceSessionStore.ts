import { create } from 'zustand';
import i18n from 'i18next';
import * as api from '@/services/deviceControlApi';
import type { DeviceSession, DeviceSessionDetail } from '@/types/deviceControl';

export const SESSION_PAGE_SIZE = 20;
export const STEP_PAGE_SIZE = 100;
const REQUEST_TIMEOUT_MS = 15000;

interface SessionState {
  ownerProjectId: string | null;
  deviceId: string | null;
  sessions: DeviceSession[];
  total: number;
  listPage: number;
  isListLoading: boolean;
  listError: string | null;
  selectedId: string | null;
  detail: DeviceSessionDetail | null;
  stepPage: number;
  isDetailLoading: boolean;
  detailError: string | null;
  reset: (projectId?: string | null, deviceId?: string | null) => void;
  loadSessions: (page?: number, deviceId?: string | null) => Promise<void>;
  selectSession: (id: string | null) => Promise<void>;
  loadDetail: (page?: number) => Promise<void>;
}

// Even a transport that ignores abort cannot leave the UI loading forever.
async function bounded<T>(controller: AbortController, request: Promise<T>): Promise<T> {
  let rejectAbort: () => void = () => {};
  const aborted = new Promise<never>((_resolve, reject) => {
    rejectAbort = () => reject(new Error('Request aborted'));
    controller.signal.addEventListener('abort', rejectAbort, { once: true });
    if (controller.signal.aborted) rejectAbort();
  });
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await Promise.race([request, aborted]);
  } finally {
    clearTimeout(timer);
    controller.signal.removeEventListener('abort', rejectAbort);
  }
}

export const useDeviceSessionStore = create<SessionState>((set, get) => {
  let listRequest: AbortController | null = null;
  let detailRequest: AbortController | null = null;
  return {
    ownerProjectId: null, deviceId: null, sessions: [], total: 0,
    listPage: 0, isListLoading: false, listError: null,
    selectedId: null, detail: null, stepPage: 0, isDetailLoading: false, detailError: null,
    reset: (ownerProjectId = null, deviceId = null) => {
      listRequest?.abort();
      detailRequest?.abort();
      listRequest = detailRequest = null;
      set({ ownerProjectId, deviceId, sessions: [], total: 0, listPage: 0,
        isListLoading: false, listError: null, selectedId: null, detail: null,
        stepPage: 0, isDetailLoading: false, detailError: null });
    },
    loadSessions: async (page = get().listPage, deviceId = get().deviceId) => {
      if (!get().ownerProjectId) return;
      listRequest?.abort();
      const request = new AbortController();
      listRequest = request;
      const changed = page !== get().listPage || deviceId !== get().deviceId;
      set({ deviceId, listPage: page, isListLoading: true, listError: null,
        ...(changed ? { sessions: [], total: 0 } : {}) });
      try {
        const result = await bounded(request, api.listDeviceSessions({
          device_id: deviceId ?? undefined, skip: page * SESSION_PAGE_SIZE, limit: SESSION_PAGE_SIZE,
        }, { signal: request.signal }));
        if (!Array.isArray(result.sessions) || !Number.isInteger(result.total) || result.total < 0) {
          throw new Error('Invalid session list');
        }
        if (listRequest === request) set({ sessions: result.sessions, total: result.total });
      } catch {
        if (listRequest === request) set({ listError: i18n.t('deviceControl.sessions.loadFailed') });
      } finally {
        if (listRequest === request) { listRequest = null; set({ isListLoading: false }); }
      }
    },
    selectSession: async (id) => {
      detailRequest?.abort();
      detailRequest = null;
      set({ selectedId: id, detail: null, stepPage: 0, detailError: null, isDetailLoading: false });
      if (id) await get().loadDetail(0);
    },
    loadDetail: async (page = get().stepPage) => {
      const id = get().selectedId;
      if (!id || !get().ownerProjectId) return;
      detailRequest?.abort();
      const request = new AbortController();
      detailRequest = request;
      const changed = page !== get().stepPage;
      set({ stepPage: page, isDetailLoading: true, detailError: null,
        ...(changed ? { detail: null } : {}) });
      try {
        const detail = await bounded(request, api.getDeviceSession(id, {
          step_skip: page * STEP_PAGE_SIZE, step_limit: STEP_PAGE_SIZE,
        }, { signal: request.signal }));
        if (detail.id !== id || !Array.isArray(detail.steps) || !Number.isInteger(detail.step_total)) {
          throw new Error('Invalid session detail');
        }
        if (detailRequest === request) set({ detail });
      } catch {
        if (detailRequest === request) set({ detailError: i18n.t('deviceControl.sessions.detailFailed') });
      } finally {
        if (detailRequest === request) { detailRequest = null; set({ isDetailLoading: false }); }
      }
    },
  };
});
