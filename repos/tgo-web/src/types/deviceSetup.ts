export type DeviceScenarioId = 'files' | 'report';

/** One in-memory selection, scoped to the employee who began configuration. */
export interface DeviceSetupHandoff {
  scenarioId: DeviceScenarioId;
  deviceId: string;
  deviceName: string;
  projectId: string;
  staffId: string;
  role: 'user' | 'admin' | 'agent';
}
