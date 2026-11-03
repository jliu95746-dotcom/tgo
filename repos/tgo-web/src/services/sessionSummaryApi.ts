import apiClient from './api';
import type { SessionHandoffSummary } from '@/types/sessionHandoff';

export const sessionSummaryApi = {
  current: (visitorId: string, signal?: AbortSignal) =>
    apiClient.get<SessionHandoffSummary>(
      `/v1/sessions/visitor/${encodeURIComponent(visitorId)}/handoff-summary`,
      { signal },
    ),
};
