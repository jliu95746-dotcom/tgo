import apiClient from './api';
import type { WorkflowScheduleApi, SchedulePreview, ScheduleState,
  ScheduleReceipt, ScheduleOccurrenceList } from '@/types/workflowSchedules';

const root = (id: string) => `/v1/ai/workflows/${encodeURIComponent(id)}/schedule`;
export const workflowScheduleApi: WorkflowScheduleApi = {
  getState: (id, signal) => apiClient.get<ScheduleState>(root(id), { signal }),
  getHistory: (id, offset, signal) => apiClient.get<ScheduleOccurrenceList>(
    `${root(id)}/occurrences?limit=10&offset=${offset}`, { signal },
  ),
  preview: (id, request, signal) => apiClient.post<SchedulePreview>(`${root(id)}/preview`, request, { signal }),
  save: (id, request, signal) => apiClient.put<ScheduleReceipt>(root(id), request, { signal }),
  disable: (id, request, signal) => apiClient.post<ScheduleReceipt>(`${root(id)}/disable`, request, { signal }),
  getCommand: (id, request, signal) => apiClient.get<ScheduleReceipt>(
    `${root(id)}/commands/${encodeURIComponent(request)}`, { signal },
  ),
};
