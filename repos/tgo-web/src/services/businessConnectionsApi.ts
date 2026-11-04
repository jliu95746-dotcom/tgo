import apiClient from './api';
import type { BusinessConnection, BusinessConnectionCreate, BusinessConnectionUpdate, BusinessConnectionTestRequest, BusinessConnectionTestReport } from '@/types/businessConnections';

export const BusinessConnectionsApi = {
  created: (requestId: string) => apiClient.get<BusinessConnection>(`/v1/business-connections/requests/${encodeURIComponent(requestId)}`),
  test: (id: string, request: BusinessConnectionTestRequest, signal?: AbortSignal) => apiClient.post<BusinessConnectionTestReport>(`/v1/business-connections/${encodeURIComponent(id)}/tests`, request, { signal }),
  tests: (id: string) => apiClient.get<BusinessConnectionTestReport[]>(`/v1/business-connections/${encodeURIComponent(id)}/tests`),
  testReport: (id: string, requestId: string) => apiClient.get<BusinessConnectionTestReport>(`/v1/business-connections/${encodeURIComponent(id)}/tests/${encodeURIComponent(requestId)}`),
  cancelTest: (id: string, requestId: string) => apiClient.post<BusinessConnectionTestReport>(`/v1/business-connections/${encodeURIComponent(id)}/tests/${encodeURIComponent(requestId)}/cancel`),
  list: (offset = 0) => apiClient.get<BusinessConnection[]>(`/v1/business-connections?limit=100&offset=${offset}`),
  get: (id: string) => apiClient.get<BusinessConnection>(`/v1/business-connections/${encodeURIComponent(id)}`),
  create: (request: BusinessConnectionCreate, signal?: AbortSignal) => apiClient.post<BusinessConnection>('/v1/business-connections', request, { signal }),
  update: (id: string, request: BusinessConnectionUpdate) => apiClient.patch<BusinessConnection>(`/v1/business-connections/${encodeURIComponent(id)}`, request),
  disable: (id: string, revision: number) => apiClient.post<BusinessConnection>(`/v1/business-connections/${encodeURIComponent(id)}/disable`, { expected_revision: revision }),
};
