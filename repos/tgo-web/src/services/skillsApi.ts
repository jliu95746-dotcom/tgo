/**
 * Skills API Service
 *
 * Handles all API interactions for skill management (CRUD + sub-files).
 * Skills are file-system-based on the backend (SKILL.md + scripts/ + references/).
 */

import { BaseApiService } from './base/BaseApiService';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Lightweight summary returned in list responses. */
export interface SkillSummary {
  name: string;
  description: string;
  author: string | null;
  is_official: boolean;
  is_featured: boolean;
  tags: string[];
  updated_at: string | null;
  enabled: boolean;
  skill_type: 'standard' | 'humanization';
  display_name: string | null;
  pending_training_count: number;
  published_version: number;
}

/** Response for a skill toggle operation. */
export interface SkillToggleResponse {
  name: string;
  enabled: boolean;
}

/** Full detail including instructions and file listings. */
export interface SkillDetail extends SkillSummary {
  instructions: string;
  license: string | null;
  version: string | null;
  metadata: Record<string, string> | null;
  scripts: string[];
  references: string[];
}

/** Request body for creating a new skill. */
export interface SkillCreateRequest {
  name: string;
  description: string;
  instructions?: string;
  author?: string;
  license?: string;
  tags?: string[];
  is_featured?: boolean;
  metadata?: Record<string, string>;
  scripts?: Record<string, string>;
  references?: Record<string, string>;
}

/** Request body for updating an existing skill. */
export interface SkillUpdateRequest {
  description?: string;
  instructions?: string;
  author?: string;
  license?: string;
  tags?: string[];
  is_featured?: boolean;
  metadata?: Record<string, string>;
}

/** Request body for importing a skill from GitHub. */
export interface SkillImportRequest {
  github_url: string;
  github_token?: string;
}

export interface HumanizationSkillCreateRequest {
  name?: string;
  display_name: string;
  description: string;
}

export interface HumanizationTrainingSampleRequest {
  customer_message: string;
  ai_draft: string;
  final_reply: string;
  source_message_id?: string;
}

export interface HumanizationTrainingStatus {
  name: string;
  pending_training_count: number;
  published_version: number;
}

export interface HumanizationTrainingApplyResponse
  extends HumanizationTrainingStatus {
  applied_count: number;
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export class SkillsApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    SKILLS: `/${this.apiVersion}/ai/skills`,
    SKILLS_IMPORT: `/${this.apiVersion}/ai/skills/import`,
    HUMANIZATION_SKILLS: `/${this.apiVersion}/ai/skills/humanization`,
    SKILL_BY_NAME: (name: string) => `/${this.apiVersion}/ai/skills/${name}`,
    SKILL_TOGGLE: (name: string) =>
      `/${this.apiVersion}/ai/skills/${name}/toggle`,
    HUMANIZATION_TRAINING: (name: string) =>
      `/${this.apiVersion}/ai/skills/${name}/training-samples`,
    HUMANIZATION_APPLY: (name: string) =>
      `/${this.apiVersion}/ai/skills/${name}/apply-training`,
    SKILL_FILE: (name: string, filePath: string) =>
      `/${this.apiVersion}/ai/skills/${name}/files/${filePath}`,
  } as const;

  /** Get raw content of a skill file (e.g. SKILL.md, scripts/main.py). */
  static async getSkillFile(name: string, filePath: string): Promise<string> {
    const service = new SkillsApiService();
    const response = await service.getResponse(
      service.endpoints.SKILL_FILE(name, filePath),
    );
    return response.text();
  }

  // -----------------------------------------------------------------------
  // Skill CRUD
  // -----------------------------------------------------------------------

  /** List all skills visible to the current project (private + official). */
  static async listSkills(): Promise<SkillSummary[]> {
    const service = new SkillsApiService();
    return service.get<SkillSummary[]>(service.endpoints.SKILLS);
  }

  /** Get full detail of a single skill. */
  static async getSkill(name: string): Promise<SkillDetail> {
    const service = new SkillsApiService();
    return service.get<SkillDetail>(service.endpoints.SKILL_BY_NAME(name));
  }

  /** Create a new project-private skill. */
  static async createSkill(data: SkillCreateRequest): Promise<SkillDetail> {
    const service = new SkillsApiService();
    return service.post<SkillDetail>(service.endpoints.SKILLS, data);
  }

  static async createHumanizationSkill(
    data: HumanizationSkillCreateRequest,
  ): Promise<SkillDetail> {
    const service = new SkillsApiService();
    return service.post<SkillDetail>(service.endpoints.HUMANIZATION_SKILLS, data);
  }

  static async addHumanizationTrainingSample(
    name: string,
    data: HumanizationTrainingSampleRequest,
  ): Promise<HumanizationTrainingStatus> {
    const service = new SkillsApiService();
    return service.post<HumanizationTrainingStatus>(
      service.endpoints.HUMANIZATION_TRAINING(name),
      data,
    );
  }

  static async applyHumanizationTraining(
    name: string,
  ): Promise<HumanizationTrainingApplyResponse> {
    const service = new SkillsApiService();
    return service.post<HumanizationTrainingApplyResponse>(
      service.endpoints.HUMANIZATION_APPLY(name),
      {},
    );
  }

  /** Import a skill from a GitHub directory URL. */
  static async importSkill(data: SkillImportRequest): Promise<SkillDetail> {
    const service = new SkillsApiService();
    return service.post<SkillDetail>(service.endpoints.SKILLS_IMPORT, data);
  }

  /** Update an existing project-private skill. */
  static async updateSkill(
    name: string,
    data: SkillUpdateRequest,
  ): Promise<SkillDetail> {
    const service = new SkillsApiService();
    return service.patch<SkillDetail>(
      service.endpoints.SKILL_BY_NAME(name),
      data,
    );
  }

  /** Toggle a skill's enabled/disabled state. */
  static async toggleSkill(
    name: string,
    enabled: boolean,
  ): Promise<SkillToggleResponse> {
    const service = new SkillsApiService();
    return service.put<SkillToggleResponse>(
      service.endpoints.SKILL_TOGGLE(name),
      { enabled },
    );
  }

  /** Delete a project-private skill. */
  static async deleteSkill(name: string): Promise<void> {
    const service = new SkillsApiService();
    return service.delete<void>(service.endpoints.SKILL_BY_NAME(name));
  }
}

export default SkillsApiService;
