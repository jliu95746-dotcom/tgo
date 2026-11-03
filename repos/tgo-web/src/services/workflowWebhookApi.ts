import apiClient, { getApiBaseUrl } from './api';
import type { WorkflowWebhookApi, WebhookState, WebhookReceipt, WebhookDeliveryList } from '@/types/workflowWebhooks';

const root = (id: string) => `/v1/ai/workflows/${encodeURIComponent(id)}/webhook`;
export const webhookIngressUrl = (id: string): string => new URL(
  `${getApiBaseUrl().replace(/\/$/, '')}/v1/workflow-webhooks/${encodeURIComponent(id)}`, window.location.origin,
).href;
export const workflowWebhookApi: WorkflowWebhookApi = {
  getState: (id, signal) => apiClient.get<WebhookState>(root(id), { signal }),
  getHistory: (id, offset, signal) => apiClient.get<WebhookDeliveryList>(`${root(id)}/deliveries?limit=10&offset=${offset}`, { signal }),
  save: (id, request, signal) => apiClient.put<WebhookReceipt>(root(id), request, { signal }),
  disable: (id, request, signal) => apiClient.post<WebhookReceipt>(`${root(id)}/disable`, request, { signal }),
  rotate: (id, request, signal) => apiClient.post<WebhookReceipt>(`${root(id)}/rotate`, request, { signal }),
  getCommand: (id, request, signal) => apiClient.get<WebhookReceipt>(`${root(id)}/commands/${encodeURIComponent(request)}`, { signal }),
};
