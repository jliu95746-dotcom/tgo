import apiClient from './api';
import type { CustomerIdentityBinding, CustomerIdentityPolicy, CustomerIdentityPolicyWrite } from '@/types/businessCustomerIdentity';

const root = (id:string) => `/v1/business-connections/${encodeURIComponent(id)}`;
export const BusinessCustomerIdentityApi = {
  policy: (id:string) => apiClient.get<CustomerIdentityPolicy>(`${root(id)}/customer-identity-policy`),
  save: (id:string,body:CustomerIdentityPolicyWrite) => apiClient.put<CustomerIdentityPolicy>(`${root(id)}/customer-identity-policy`,body),
  bindings: (id:string) => apiClient.get<CustomerIdentityBinding[]>(`${root(id)}/customer-identities`),
  revoke: (id:string,binding:string,revision:number) => apiClient.post<CustomerIdentityBinding>(`${root(id)}/customer-identities/${encodeURIComponent(binding)}/revoke`,{expected_revision:revision}),
};
