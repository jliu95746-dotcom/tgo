import 'package:dio/dio.dart';
import '../models/api_types.dart';
import 'api_client.dart';

/// Service for loading message history
class HistoryService {
  final String apiBase;
  final Dio _dio;

  HistoryService({
    required this.apiBase,
    Dio? dio,
  }) : _dio = dio ?? ApiClient.createDio(apiBase: apiBase);

  /// Sync visitor messages from server
  ///
  /// [platformApiKey] - Platform API key for authentication
  /// [channelId] - Channel ID
  /// [channelType] - Channel type (251 for customer service)
  /// [startSeq] - Start sequence number (null for latest)
  /// [endSeq] - End sequence number (null for no limit)
  /// [limit] - Maximum number of messages to fetch
  /// [pullMode] - 0: pull down (older), 1: pull up (newer)
  Future<MessageSyncResponse> syncMessages({
    required String platformApiKey,
    required String channelId,
    required int channelType,
    int? startSeq,
    int? endSeq,
    int? limit,
    int? pullMode,
  }) async {
    const url = 'v1/visitors/messages/sync';

    try {
      final response = await _dio.post<Map<String, dynamic>>(
        url,
        data: {
          'platform_api_key': platformApiKey,
          'channel_id': channelId,
          'channel_type': channelType,
          if (startSeq != null) 'start_message_seq': startSeq,
          if (endSeq != null) 'end_message_seq': endSeq,
          if (limit != null) 'limit': limit,
          if (pullMode != null) 'pull_mode': pullMode,
        },
      );

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300 || response.data == null) {
        throw Exception('Sync failed: ${response.statusCode}');
      }

      return MessageSyncResponse.fromJson(response.data!);
    } on DioException {
      rethrow;
    }
  }

  /// Fetch channel info (for staff names/avatars)
  Future<ChannelInfo?> fetchChannelInfo({
    required String platformApiKey,
    required String channelId,
    int channelType = 1,
  }) async {
    const url = 'v1/channels/info';

    try {
      final response = await _dio.get<Map<String, dynamic>>(
        url,
        queryParameters: {
          'platform_api_key': platformApiKey,
          'channel_id': channelId,
          'channel_type': channelType,
        },
      );

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300 || response.data == null) {
        return null;
      }

      return ChannelInfo.fromJson(response.data!);
    } catch (_) {
      return null;
    }
  }
}

