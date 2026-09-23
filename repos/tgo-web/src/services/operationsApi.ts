import { createScopedApiClient } from './api';
import type { TrialCodeIssue, TrialCodeRecord } from '../types/trialActivation';
import type { CommercialReadiness, OperationsAudit, OperationsTask } from '../types/operationsTasks';
import type { CreditAdjustment, InvoiceRequest, OperationsCompany, QuotaReply, Reconciliation, Refund, RefundInput } from '../types/billingSupport';
import type { BillingOrder, BillingPlan, PlanDefinition } from '../types/billing';
import type {
  MigrationPreviewResponse, OperatorLoginRequest, OperatorLoginResponse,
  OperatorProfile, OperationsStatus,
} from '../types/operations';

let onSessionExpired: () => void = () => {};
const client = createScopedApiClient('yujian-operations-token', () => {
  client.setToken(null);
  onSessionExpired();
});

export const operationsApi = {
  issueTrialCode: () => client.post<TrialCodeIssue>('/v1/ops/trial-codes'),
  trialCodes: () => client.get<TrialCodeRecord[]>('/v1/ops/trial-codes'),
  commercialHealth: () => client.get<import('../types/commercialHealth').CommercialHealth>('/v1/ops/commercial-health'),
  modelPolicy: () => client.get<import('../types/platformModels').PlatformModelPolicy>('/v1/ops/model-policy'),
  saveModelPolicy: (payload: import('../types/platformModels').PlatformModelChange) => client.put<import('../types/platformModels').PlatformModelPolicy>('/v1/ops/model-policy', payload),
  trialPolicy: () => client.get<import('../types/trialPolicy').TrialPolicy>('/v1/ops/trial-policy'),
  updateTrialPolicy: (payload: { expected_version: number; ai_replies: number; reason: string }) => client.patch<import('../types/trialPolicy').TrialPolicy>('/v1/ops/trial-policy', payload),
  modelUsage: (offset = 0) => client.get<import('../types/modelUsage').ModelUsage[]>(`/v1/ops/model-usage?offset=${offset}`),
  commercialReadiness: () => client.get<CommercialReadiness>('/v1/ops/commercial-readiness'),
  tasks: (offset = 0) => client.get<OperationsTask[]>(`/v1/ops/tasks?offset=${offset}`),
  audits: (offset = 0) => client.get<OperationsAudit[]>(`/v1/ops/audits?offset=${offset}`),
  retryTask: (id: string, reason: string) => client.post<OperationsTask>(`/v1/ops/tasks/${encodeURIComponent(id)}/retry`, { reason }),
  companies: (offset = 0) => client.get<OperationsCompany[]>(`/v1/ops/companies?offset=${offset}&limit=20`),
  adjustCredits: (id: string, payload: CreditAdjustment) => client.post<void>(`/v1/ops/companies/${encodeURIComponent(id)}/credits`, payload),
  invoices: (offset = 0) => client.get<InvoiceRequest[]>(`/v1/ops/invoices?offset=${offset}`),
  processInvoice: (id: string, payload: { status: 'issued' | 'rejected'; invoice_number?: string; reason: string }) => client.patch<InvoiceRequest>(`/v1/ops/invoices/${encodeURIComponent(id)}`, payload),
  refunds: (offset = 0) => client.get<Refund[]>(`/v1/ops/refunds?offset=${offset}`),
  previewRefund: (payload: RefundInput) => client.post<Refund>('/v1/ops/refunds/preview', payload),
  confirmRefund: (id: string) => client.post<Refund>(`/v1/ops/refunds/${encodeURIComponent(id)}/confirm`, { reviewed: true }),
  cancelRefund: (id: string) => client.delete<void>(`/v1/ops/refunds/${encodeURIComponent(id)}`),
  reconciliations: (offset = 0) => client.get<Reconciliation[]>(`/v1/ops/reconciliations?offset=${offset}`),
  quotaReviews: (offset = 0) => client.get<QuotaReply[]>(`/v1/ops/usage/review?offset=${offset}`),
  resolveQuota: (id: string, payload: { action: 'settle' | 'release'; reason: string }) => client.post<void>(`/v1/ops/usage/${encodeURIComponent(id)}/resolve`, payload),
  companyState: (id: string, payload: { action: 'suspend' | 'restore'; reason: string }) => client.post<void>(`/v1/ops/companies/${encodeURIComponent(id)}/state`, payload),
  plans: () => client.get<BillingPlan[]>('/v1/ops/plans'),
  createPlan: (code: string, definition: PlanDefinition) => client.post<BillingPlan>('/v1/ops/plans', { code, definition }),
  changePlanState: (id: string, state: 'published' | 'retired') => client.post<BillingPlan>(`/v1/ops/plans/${encodeURIComponent(id)}/${state}`),
  orders: (offset = 0) => client.get<BillingOrder[]>(`/v1/ops/orders?offset=${offset}&limit=100`),
  onSessionExpired(handler: () => void) { onSessionExpired = handler; },
  hasSession: () => Boolean(client.getToken()),
  clearSession: () => client.setToken(null),
  status: () => client.get<OperationsStatus>('/v1/ops/status'),
  me: () => client.get<OperatorProfile>('/v1/ops/me'),
  async login(data: OperatorLoginRequest): Promise<OperatorProfile> {
    const response = await client.post<OperatorLoginResponse>('/v1/ops/login', data);
    client.setToken(response.access_token);
    return response.operator;
  },
  async logout(): Promise<void> {
    await client.post<void>('/v1/ops/logout');
    client.setToken(null);
  },
  preview: (offset = 0) => client.get<MigrationPreviewResponse>(
    `/v1/ops/migration-preview?limit=20&offset=${offset}`,
  ),
};
