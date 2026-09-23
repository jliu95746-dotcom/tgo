import { apiClient } from './api';

interface EmailActionResult { message: string }

export const companyEmailApi = {
  verify: (token: string) => apiClient.post<EmailActionResult>('/v1/staff/verify-email', { token }),
  verifyCode: (email: string, code: string) => apiClient.post<EmailActionResult>(
    '/v1/staff/verify-email-code', { email, code },
  ),
  reset: (token: string, password: string) => apiClient.post<EmailActionResult>('/v1/staff/reset-password', { token, password }),
  request: (email: string, reset: boolean) => apiClient.post<EmailActionResult>(
    reset ? '/v1/staff/forgot-password' : '/v1/staff/resend-verification', { email },
  ),
};
