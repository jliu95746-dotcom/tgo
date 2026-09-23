import { apiClient } from './api';
import type { InvoiceInput, InvoiceRequest, QuotaBatch, QuotaReply } from '../types/billingSupport';
import type { BillingOrder, BillingPlan, BillingQuote, BillingSubscription, QuoteRequest } from '../types/billing';

export const billingApi = {
  redeemTrialCode: (code: string) => apiClient.post<{ message: string }>(
    '/v1/company/trial-activation', { code },
  ),
  invoices: (offset = 0) => apiClient.get<InvoiceRequest[]>(`/v1/billing/invoices?offset=${offset}`),
  requestInvoice: (payload: InvoiceInput) => apiClient.post<InvoiceRequest>('/v1/billing/invoices', payload),
  batches: (offset = 0) => apiClient.get<QuotaBatch[]>(`/v1/billing/usage/batches?offset=${offset}`),
  replies: (offset = 0) => apiClient.get<QuotaReply[]>(`/v1/billing/usage/replies?offset=${offset}`),
  plans: () => apiClient.get<BillingPlan[]>('/v1/billing/plans'),
  subscription: () => apiClient.get<BillingSubscription>('/v1/billing/subscription'),
  quote: (payload: QuoteRequest) => apiClient.post<BillingQuote>('/v1/billing/quotes', payload),
  createOrder: (quoteId: string) => apiClient.post<BillingOrder>('/v1/billing/orders', { quote_id: quoteId }),
  checkout: (id: string) => apiClient.post<BillingOrder>(`/v1/billing/orders/${encodeURIComponent(id)}/wechat`),
  order: (id: string) => apiClient.get<BillingOrder>(`/v1/billing/orders/${encodeURIComponent(id)}`),
  orders: (offset = 0) => apiClient.get<BillingOrder[]>(`/v1/billing/orders?offset=${offset}&limit=20`),
};
