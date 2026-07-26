/**
 * Visitor API Service
 * Handles visitor-related API endpoints
 */

import { BaseApiService } from './base/BaseApiService';
import { apiClient } from './api';
import type { Visitor, ChannelAIInsights, VisitorAISettings } from '@/types';

export type VisitorServiceMode = 'auto' | 'assist' | 'manual';

// API Request/Response Types based on OpenAPI specification

// Tag response structure from API
export interface TagResponse {
  id: string;
  name: string;
  category: string;
  weight: number;
  color?: string | null;
  description?: string | null;
  project_id: string;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

// Extended tag response with visitor_tag_id for deletion
export interface VisitorTagWithAssociationId extends TagResponse {
  /** Visitor-tag association ID (UUID) - needed for deletion */
  visitor_tag_id?: string;
}

// Visitor attributes update request (PUT /v1/visitors/{visitor_id}/attributes)
export interface VisitorAttributesUpdateRequest {
  name?: string | null;
  nickname?: string | null;
  avatar_url?: string | null;
  phone_number?: string | null;
  email?: string | null;
  company?: string | null;
  job_title?: string | null;
  platform_type?: string | null;
  source?: string | null;
  note?: string | null;
  custom_attributes?: Record<string, string | null> | null;
}

// Complete visitor response structure from API (GET /v1/visitors/{visitor_id})
export interface VisitorResponse {
  id: string;
  project_id: string;
  platform_id: string;
  platform_open_id: string;
  name?: string | null;
  nickname?: string | null;
  nickname_zh?: string | null;
  display_nickname?: string | null;
  avatar_url?: string | null;
  phone_number?: string | null;
  email?: string | null;
  company?: string | null;
  job_title?: string | null;
  platform_type?: string | null;
  source?: string | null;
  note?: string | null;
  custom_attributes: Record<string, string | null>;
  first_visit_time: string;
  last_visit_time: string;
  last_offline_time?: string | null;
  last_online_duration_minutes?: number | null;
  is_online: boolean;
  ai_disabled?: boolean; // True if AI is disabled for this visitor
  service_mode?: VisitorServiceMode | null;
  humanization_skill_name?: string | null;
  humanization_skill_enabled: boolean;
  ai_settings?: VisitorAISettings | null;
  tags: TagResponse[];
  ai_profile?: any | null;
  ai_insights?: ChannelAIInsights | null;
  system_info?: any | null;
  recent_activities: any[];
  // Additional fields from API
  language?: string | null;
  timezone?: string | null;
  ip_address?: string | null;
  display_location?: string | null;
  geo_country?: string | null;
  geo_country_code?: string | null;
  geo_region?: string | null;
  geo_city?: string | null;
  geo_isp?: string | null;
  service_status?: string | null;
  assigned_staff_id?: string | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

// Request params for GET /v1/visitors/by-channel
export interface VisitorByChannelParams {
  channel_id: string;
  channel_type: number; // 1 for personal; 251 for customer service
}

// Response for POST /v1/visitors/{visitor_id}/avatar
export interface VisitorAvatarUploadResponse {
  visitor_id: string;
  avatar_url: string;
  file_name: string;
  file_size: number;
  file_type: string;
  uploaded_at: string;
}


// Request params for GET /v1/visitors
export interface VisitorListParams {
  platform_id?: string;
  is_online?: boolean;
  recent_online_minutes?: number;
  service_status?: string[];
  search?: string;
  limit?: number;
  offset?: number;
  tag_ids?: string[];
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

// Response for GET /v1/visitors
export interface VisitorListResponse {
  data: VisitorResponse[];
  pagination: {
    total: number;
    limit: number;
    offset: number;
    has_next: boolean;
    has_prev: boolean;
  };
}

// Response for POST /v1/sessions/visitor/{visitor_id}/close
export interface VisitorSessionResponse {
  id: string;
  project_id: string;
  visitor_id: string;
  channel_id: string;
  channel_type: number;
  status: string;
  started_at: string;
  ended_at?: string | null;
  created_at: string;
  updated_at: string;
}

// Request for POST /v1/sessions/visitor/{visitor_id}/transfer
export interface TransferSessionRequest {
  target_staff_id: string;
  reason?: string | null;
}

// Response for POST /v1/sessions/visitor/{visitor_id}/transfer
export interface TransferSessionResponse {
  success: boolean;
  message: string;
  old_session_id: string;
  new_session_id?: string | null;
  visitor_id: string;
  from_staff_id: string;
  to_staff_id: string;
}

/**
 * Visitor API Service Class
 */
class VisitorApiService extends BaseApiService {
  protected readonly endpoints = {
    visitors: '/v1/visitors',
    visitorById: (id: string) => `/v1/visitors/${id}`,
    visitorAttributes: (id: string) => `/v1/visitors/${id}/attributes`,
    visitorAvatar: (id: string) => `/v1/visitors/${id}/avatar`,
    visitorByChannel: '/v1/visitors/by-channel',
    enableAI: (id: string) => `/v1/visitors/${id}/enable-ai`,
    disableAI: (id: string) => `/v1/visitors/${id}/disable-ai`,
    serviceMode: (id: string) => `/v1/visitors/${id}/service-mode`,
    closeSession: (visitorId: string) => `/v1/sessions/visitor/${visitorId}/close`,
    transferSession: (visitorId: string) => `/v1/sessions/visitor/${visitorId}/transfer`,
  };

  protected readonly apiVersion = 'v1';

  /**
   * List visitors with pagination and filters
   */
  async listVisitors(params: VisitorListParams): Promise<VisitorListResponse> {
    const endpoint = this.endpoints.visitors;
    const qp = new URLSearchParams();
    
    // Add query parameters
    if (params.platform_id) qp.set('platform_id', params.platform_id);
    if (params.is_online !== undefined) qp.set('is_online', String(params.is_online));
    if (params.recent_online_minutes !== undefined) qp.set('recent_online_minutes', String(params.recent_online_minutes));
    if (params.service_status && params.service_status.length > 0) {
      params.service_status.forEach(status => qp.append('service_status', status));
    }
    if (params.search) qp.set('search', params.search);
    if (params.limit !== undefined) qp.set('limit', String(params.limit));
    if (params.offset !== undefined) qp.set('offset', String(params.offset));
    if (params.sort_by) qp.set('sort_by', params.sort_by);
    if (params.sort_order) qp.set('sort_order', params.sort_order);
    
    // Add multiple tag_ids as query parameters: ?tag_ids=id1&tag_ids=id2
    if (params.tag_ids && params.tag_ids.length > 0) {
      params.tag_ids.forEach(id => qp.append('tag_ids', id));
    }

    const url = `${endpoint}${qp.toString() ? `?${qp.toString()}` : ''}`;
    return this.get<VisitorListResponse>(url);
  }

  /**
   * Get visitor by ID
   */
  async getVisitor(visitorId: string): Promise<VisitorResponse> {
    const endpoint = this.endpoints.visitorById(visitorId);
    return this.get<VisitorResponse>(endpoint);
  }

  /**
   * Update visitor attributes using PUT /v1/visitors/{visitor_id}/attributes
   */
  async updateVisitorAttributes(
    visitorId: string,
    attributes: VisitorAttributesUpdateRequest
  ): Promise<VisitorResponse> {
    const endpoint = this.endpoints.visitorAttributes(visitorId);
    // The API expects the exact format as defined in VisitorAttributesUpdateRequest
    return this.put<VisitorResponse>(endpoint, attributes);
  }

  /**
   * Get visitor by channel using GET /v1/visitors/by-channel
   */
  async getVisitorByChannel(params: VisitorByChannelParams): Promise<VisitorResponse> {
    const endpoint = this.endpoints.visitorByChannel;
    const url = `${endpoint}?channel_id=${encodeURIComponent(params.channel_id)}&channel_type=${params.channel_type}`;
    return this.get<VisitorResponse>(url);
  }

  /**
   * Enable AI for a visitor using POST /v1/visitors/{visitor_id}/enable-ai
   */
  async enableAI(visitorId: string): Promise<VisitorResponse> {
    const endpoint = (this.endpoints.enableAI as (id: string) => string)(visitorId);
    return this.post<VisitorResponse>(endpoint, {});
  }

  /**
   * Disable AI for a visitor using POST /v1/visitors/{visitor_id}/disable-ai
   */
  async disableAI(visitorId: string): Promise<VisitorResponse> {
    const endpoint = (this.endpoints.disableAI as (id: string) => string)(visitorId);
    return this.post<VisitorResponse>(endpoint, {});
  }

  async updateServiceMode(
    visitorId: string,
    data: {
      service_mode: VisitorServiceMode;
      humanization_skill_name?: string | null;
      humanization_skill_enabled: boolean;
    },
  ): Promise<VisitorResponse> {
    return this.put<VisitorResponse>(this.endpoints.serviceMode(visitorId), data);
  }

  /**
   * Close current session for a visitor using POST /v1/sessions/visitor/{visitor_id}/close
   * @param visitorId - The visitor's UUID
   * @returns Promise with session response
   */
  async closeSession(visitorId: string): Promise<VisitorSessionResponse> {
    const endpoint = (this.endpoints.closeSession as (id: string) => string)(visitorId);
    return this.post<VisitorSessionResponse>(endpoint, {});
  }

  /**
   * Transfer current session for a visitor to another staff
   * @param visitorId - The visitor's UUID
   * @param targetStaffId - The target staff's UUID
   * @param reason - Optional reason for transfer
   * @returns Promise with transfer response
   */
  async transferSession(
    visitorId: string,
    targetStaffId: string,
    reason?: string
  ): Promise<TransferSessionResponse> {
    const endpoint = (this.endpoints.transferSession as (id: string) => string)(visitorId);
    const body: TransferSessionRequest = {
      target_staff_id: targetStaffId,
      reason: reason || null,
    };
    return this.post<TransferSessionResponse>(endpoint, body);
  }

  /**
   * Upload visitor avatar using POST /v1/visitors/{visitor_id}/avatar
   * @param visitorId - The visitor's UUID
   * @param file - The image file to upload (JPEG, PNG, GIF, WebP; max 5MB)
   */
  async uploadAvatar(visitorId: string, file: File): Promise<VisitorAvatarUploadResponse> {
    const endpoint = (this.endpoints.visitorAvatar as (id: string) => string)(visitorId);
    const formData = new FormData();
    formData.append('file', file);
    
    try {
      return await apiClient.postFormData<VisitorAvatarUploadResponse>(endpoint, formData);
    } catch (error) {
      console.error('Upload visitor avatar failed:', error);
      throw new Error(this.handleApiError(error));
    }
  }

  /**
   * Transform API response to internal Visitor type
   */
  transformToVisitor(apiResponse: VisitorResponse): Visitor {
    return {
      id: apiResponse.id,
      name: apiResponse.name || '',
      email: apiResponse.email || undefined,
      phone: apiResponse.phone_number || undefined,
      avatar: apiResponse.avatar_url || '',
      status: apiResponse.is_online ? 'online' : 'offline',
      tags: apiResponse.tags.map(tag => tag.name),
      notes: apiResponse.note || undefined,
      firstVisit: apiResponse.first_visit_time,
      lastVisit: apiResponse.last_visit_time,
      visitCount: undefined, // Not provided by API
      pageViews: undefined, // Not provided by API
      sessionDuration: undefined, // Not provided by API
    };
  }

  /**
   * Transform internal visitor data to API request format
   */
  transformToApiRequest(visitor: Partial<Visitor>): VisitorAttributesUpdateRequest {
    return {
      name: visitor.name,
      email: visitor.email,
      phone_number: visitor.phone,
      note: visitor.notes,
      // Transform any additional properties to custom_attributes
      custom_attributes: {}
    };
  }
}

// Create and export service instance
export const visitorApiService = new VisitorApiService();
