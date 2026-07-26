import { BaseApiService } from './base/BaseApiService';

// Request type based on OpenAPI docs#/components/schemas/StaffSendPlatformMessageRequest
export interface StaffSendPlatformMessageRequest {
  channel_id: string;
  channel_type: number; // WuKongIM channel type, customer service chat uses 251
  payload: Record<string, any>; // Platform Service message payload
  client_msg_no?: string | null; // Optional idempotency key
}

// Response type is not specified in OpenAPI schema (empty). Use unknown for now.
export type StaffSendPlatformMessageResponse = unknown;

// Request type for staff-to-agent chat based on OpenAPI docs
export interface StaffAgentChatRequest {
  agent_id: string; // AI Agent ID to chat with (UUID format)
  message: string; // Message content to send
  system_message?: string | null; // Optional system message/prompt
  expected_output?: string | null; // Optional expected output format
  timeout_seconds?: number | null; // Timeout in seconds (1-600, default 120)
}

// Response type for staff-to-agent chat
export interface StaffAgentChatResponse {
  success: boolean; // Whether the chat completed successfully
  message: string; // Status message
  client_msg_no: string; // Message correlation ID for tracking
}

export interface AssistDraftRequest {
  visitor_id: string;
  customer_message: string;
  humanization_skill_name?: string | null;
  source_message_id?: string | null;
}

export interface AssistDraftResponse {
  draft: string;
  humanization_skill_name?: string | null;
  source_message_id?: string | null;
}

class ChatMessagesApiService extends BaseApiService {
  protected readonly apiVersion = 'v1';
  protected readonly endpoints = {
    sendPlatformMessage: '/v1/chat/messages/send',
    agentChat: '/v1/chat/agent',
    clearMemory: '/v1/chat/memory',
    assistDraft: '/v1/chat/assist/draft',
  } as const;

  /**
   * Forward a staff-authenticated outbound message to the Platform Service.
   * This must be called before sending via WebSocket for non-website platforms.
   */
  async staffSendPlatformMessage(
    data: StaffSendPlatformMessageRequest
  ): Promise<StaffSendPlatformMessageResponse> {
    return this.post<StaffSendPlatformMessageResponse>(this.endpoints.sendPlatformMessage, data);
  }

  /**
   * Staff chat with a single AI agent.
   * The AI response is delivered via WuKongIM to the client.
   */
  async staffAgentChat(
    data: StaffAgentChatRequest
  ): Promise<StaffAgentChatResponse> {
    return this.post<StaffAgentChatResponse>(this.endpoints.agentChat, data);
  }

  async generateAssistDraft(
    data: AssistDraftRequest,
  ): Promise<AssistDraftResponse> {
    return this.post<AssistDraftResponse>(this.endpoints.assistDraft, data);
  }

  /**
   * Clear AI conversational memory for a specific channel.
   */
  async clearChatMemory(params: {
    channel_id: string;
    channel_type: number;
  }): Promise<{ success: boolean; message: string }> {
    const query = new URLSearchParams({
      channel_id: params.channel_id,
      channel_type: params.channel_type.toString(),
    }).toString();
    return this.delete<{ success: boolean; message: string }>(`${this.endpoints.clearMemory}?${query}`);
  }
}

export const chatMessagesApiService = new ChatMessagesApiService();
export default chatMessagesApiService;
