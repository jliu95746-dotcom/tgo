import type { ScenarioId, ScenarioModule } from './businessScenarios';
import type { ScenarioTemplate } from './scenarioDrafts';
import type { ScenarioReadinessReport } from './scenarioResources';

export type ScenarioPreparationKey = `${ScenarioModule}:${ScenarioId}`;
export interface ScenarioPreparationEntry {
  template: ScenarioTemplate;
  status: 'checking' | 'checked' | 'failed' | 'planned';
  report: ScenarioReadinessReport | null;
}
export interface ScenarioPreparationModule {
  loading: boolean;
  failed: boolean;
}
