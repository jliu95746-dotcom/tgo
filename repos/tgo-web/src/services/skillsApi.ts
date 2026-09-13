/**
 * Skills API Service
 *
 * Handles all API interactions for skill management (CRUD + sub-files).
 * Skills are file-system-based on the backend (SKILL.md + scripts/ + references/).
 */

import { BaseApiService } from './base/BaseApiService';
import { apiClient } from './api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Lightweight summary returned in list responses. */
export interface SkillSummary {
  used_by?: { id: string; name: string; enabled: boolean }[];
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
  display_name?: string;
  description?: string;
  instructions?: string;
  author?: string;
  license?: string;
  tags?: string[];
  is_featured?: boolean;
  metadata?: Record<string, string>;
}

/** Local file upload for an ordinary skill. */
export interface SkillImportRequest {
  file: File;
  display_name?: string;
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
  recent_messages?: ConversationTurn[];
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

export interface ConversationTurn { role: 'customer' | 'staff' | 'assistant'; content: string }
export interface TrainingExample {
  id: string;
  customer_message: string;
  ai_draft: string;
  final_reply: string;
  source_message_id: string;
  created_at: string;
  recent_messages: ConversationTurn[];
  scene_tags: string[];
  change_kind: 'expression' | 'facts' | 'mixed' | 'review';
  rules: string[];
  warnings: string[];
  selected: boolean;
}
export interface TrainingReview {
  name: string;
  published_version: number;
  snapshot_id: string;
  pending: TrainingExample[];
  published: TrainingExample[];
  rules: string[];
  analysis_error?: string | null;
}
export interface TrainingPublishRequest { snapshot_id: string; samples: TrainingExample[] }
export interface HumanizationTryRequest {
  customer_message: string;
  factual_draft: string;
  recent_messages?: ConversationTurn[];
  candidate?: TrainingPublishRequest;
}
export interface HumanizationTryResponse {
  published_reply: string;
  candidate_reply: string | null;
  published_version: number;
  matched_example_ids: string[];
  candidate_example_ids: string[];
  quality_issues: string[];
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
    data: TrainingPublishRequest,
  ): Promise<HumanizationTrainingApplyResponse> {
    const service = new SkillsApiService();
    return service.post<HumanizationTrainingApplyResponse>(
      service.endpoints.HUMANIZATION_APPLY(name),
      data,
    );
  }

  /** Import a skill from a GitHub directory URL. */
  static async reviewTraining(name: string): Promise<TrainingReview> {
    const service = new SkillsApiService();
    return service.get<TrainingReview>(`${service.endpoints.SKILL_BY_NAME(name)}/training-review`);
  }

  static async previewTraining(name: string): Promise<TrainingReview> {
    const service = new SkillsApiService();
    return service.post<TrainingReview>(`${service.endpoints.SKILL_BY_NAME(name)}/training-preview`, {});
  }

  static async tryReply(name: string, data: HumanizationTryRequest): Promise<HumanizationTryResponse> {
    const service = new SkillsApiService();
    return service.post<HumanizationTryResponse>(`${service.endpoints.SKILL_BY_NAME(name)}/try-reply`, data);
  }

  static async importSkill(data: SkillImportRequest): Promise<SkillDetail> {
    const service = new SkillsApiService();
    const form = new FormData();
    form.append('file', data.file);
    if (data.display_name) form.append('display_name', data.display_name);
    return apiClient.postFormData<SkillDetail>(service.endpoints.SKILLS_IMPORT, form);
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
