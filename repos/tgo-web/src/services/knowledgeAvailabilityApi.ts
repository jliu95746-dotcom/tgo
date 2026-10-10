import BaseApiService from './base/BaseApiService';
import type { AgentKnowledgeAvailability } from '@/types';

export class KnowledgeAvailabilityApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {};

  static async getForPlatform(platformId: string, agentId?: string): Promise<AgentKnowledgeAvailability> {
    const service = new KnowledgeAvailabilityApiService();
    const query = new URLSearchParams({ use_default: String(!agentId) });
    if (agentId) query.set('agent_id', agentId);
    return service.get<AgentKnowledgeAvailability>(
      `/v1/platforms/${platformId}/knowledge-availability?${query.toString()}`,
    );
  }
}
