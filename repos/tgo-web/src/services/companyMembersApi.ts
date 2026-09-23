import { apiClient, type StaffResponse } from './api';

export interface CompanyInvitation {
  id: string; email: string; role: 'admin' | 'user';
  status: 'pending' | 'accepted' | 'revoked'; expires_at: string; created_at: string;
}
export interface CompanySeats { used: number; reserved: number; limit: number | null }
export interface CompanyMemberChange {
  role?: 'admin' | 'user'; account_enabled?: boolean;
  transfer_to?: string; return_to_queue?: boolean;
}
export const companyMembersApi = {
  status: () => apiClient.get<{ enabled: boolean }>('/v1/company/status'),
  seats: () => apiClient.get<CompanySeats>('/v1/company/seats'),
  invitations: () => apiClient.get<CompanyInvitation[]>('/v1/company/invitations'),
  invite: (email: string, role: 'admin' | 'user') => apiClient.post<CompanyInvitation>('/v1/company/invitations', { email, role }),
  revoke: (id: string) => apiClient.delete<void>(`/v1/company/invitations/${encodeURIComponent(id)}`),
  change: (id: string, change: CompanyMemberChange) => apiClient.patch<StaffResponse>(`/v1/company/members/${encodeURIComponent(id)}`, change),
  accept: (token: string, password: string) => apiClient.post<{ message: string }>('/v1/company/invitations/accept', { token, password }),
};
