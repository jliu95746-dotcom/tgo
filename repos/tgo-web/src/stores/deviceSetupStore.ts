import { create } from 'zustand';
import { useAuthStore } from './authStore';
import type { Device } from '@/types/deviceControl';
import type { DeviceScenarioId, DeviceSetupHandoff } from '@/types/deviceSetup';

interface DeviceSetupState {
  handoff: DeviceSetupHandoff | null;
  prepare: (scenarioId: DeviceScenarioId, device: Device) => boolean;
  clear: () => void;
}

export const useDeviceSetupStore = create<DeviceSetupState>(set => ({
  handoff: null,
  prepare: (scenarioId, device) => {
    const user = useAuthStore.getState().user;
    if (!user || device.project_id !== user.project_id || device.status !== 'online'
        || !['files', 'report'].includes(scenarioId)) return false;
    set({ handoff: { scenarioId, deviceId: device.id, deviceName: device.device_name,
      projectId: user.project_id, staffId: user.id, role: user.role } });
    return true;
  },
  clear: () => set({ handoff: null }),
}));

useAuthStore.subscribe((next, previous) => {
  if (next.user?.id !== previous.user?.id || next.user?.project_id !== previous.user?.project_id
      || next.user?.role !== previous.user?.role) useDeviceSetupStore.getState().clear();
});
