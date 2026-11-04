import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import '../models/api_types.dart';
import 'api_client.dart';

/// Service for chat API calls (completion, etc.)
class ChatService {
  final String apiBase;
  final Dio _dio;

  ChatService({
    required this.apiBase,
    Dio? dio,
  }) : _dio = dio ??
            ApiClient.createDio(
              apiBase: apiBase,
              connectTimeout: const Duration(seconds: 30),
              receiveTimeout: const Duration(seconds: 60),
            );

  /// Call chat completion API
  ///
  /// This triggers the AI to process the message and respond via WebSocket
  Future<void> sendCompletion({
    required String apiKey,
    required String message,
    required String fromUid,
    String? channelId,
    int? channelType,
  }) async {
    const url = 'v1/chat/completion';

    final request = ChatCompletionRequest(
      apiKey: apiKey,
      message: message,
      fromUid: fromUid,
      channelId: channelId,
      channelType: channelType,
      wukongimOnly: true,
      forwardUserMessageToWukongim: false,
      stream: false,
    );

    debugPrint('[ChatService] Sending completion request');

    try {
      final response = await _dio.post<Map<String, dynamic>>(
        url,
        data: request.toJson(),
      );

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300) {
        throw Exception('Completion failed: ${response.statusCode}');
      }

      final data = response.data;
      if (data != null && data['event_type'] == 'error') {
        throw Exception(
            data['message'] ?? data['detail'] ?? 'Unknown error');
      }

      debugPrint('[ChatService] Completion request successful');
    } on DioException catch (e) {
      debugPrint('[ChatService] Completion request failed: ${e.response?.statusCode ?? 0}');
      rethrow;
    }
  }

  /// Cancel an ongoing AI stream
  Future<void> cancelStream({
    required String platformApiKey,
    required String clientMsgNo,
    String reason = 'user_cancel',
  }) async {
    const url = 'v1/ai/runs/cancel-by-client';

    try {
      final response = await _dio.post<Map<String, dynamic>>(
        url,
        data: {
          'platform_api_key': platformApiKey,
          'client_msg_no': clientMsgNo,
          'reason': reason,
        },
      );

      if (response.statusCode != 200) {
        debugPrint('[ChatService] Cancel stream failed: ${response.statusCode}');
      }
    } catch (_) {
      debugPrint('[ChatService] Cancel stream failed');
    }
  }
}

