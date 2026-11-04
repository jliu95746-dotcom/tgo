import 'dart:io';
import 'dart:typed_data';
import 'package:dio/dio.dart';
import '../models/api_types.dart';
import 'api_client.dart';

/// Callback for upload progress (0-100)
typedef UploadProgressCallback = void Function(int progress);

/// Service for file uploads
class UploadService {
  final String apiBase;
  final String apiKey;
  final Dio _dio;

  UploadService({
    required this.apiBase,
    required this.apiKey,
    Dio? dio,
  }) : _dio = dio ??
            ApiClient.createDio(
              apiBase: apiBase,
              apiKey: apiKey,
              connectTimeout: const Duration(seconds: 30),
              receiveTimeout: const Duration(seconds: 60),
            );

  /// Upload a file
  ///
  /// [channelId] - Channel ID
  /// [channelType] - Channel type
  /// [file] - File to upload
  /// [fileName] - File name
  /// [onProgress] - Progress callback
  /// [cancelToken] - Token to cancel the upload
  Future<UploadFileResponse> uploadFile({
    required String channelId,
    required int channelType,
    required File file,
    String? fileName,
    UploadProgressCallback? onProgress,
    CancelToken? cancelToken,
  }) async {
    const url = 'v1/chat/upload';

    final name = fileName ?? file.path.split('/').last;

    try {
      final formData = FormData.fromMap({
        'channel_id': channelId,
        'channel_type': channelType,
        'file': await MultipartFile.fromFile(file.path, filename: name),
      });

      final response = await _dio.post<Map<String, dynamic>>(
        url,
        data: formData,
        cancelToken: cancelToken,
        onSendProgress: (sent, total) {
          if (total > 0 && onProgress != null) {
            final progress = ((sent / total) * 100).round();
            onProgress(progress);
          }
        },
      );

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300 || response.data == null) {
        throw Exception('Upload failed: ${response.statusCode}');
      }

      return UploadFileResponse.fromJson(response.data!);
    } on DioException catch (e) {
      if (e.type == DioExceptionType.cancel) {
        throw Exception('Upload cancelled');
      }
      rethrow;
    }
  }

  /// Upload file bytes (for web platform)
  Future<UploadFileResponse> uploadBytes({
    required String channelId,
    required int channelType,
    required Uint8List bytes,
    required String fileName,
    String? mimeType,
    UploadProgressCallback? onProgress,
    CancelToken? cancelToken,
  }) async {
    const url = 'v1/chat/upload';

    try {
      final formData = FormData.fromMap({
        'channel_id': channelId,
        'channel_type': channelType,
        'file': MultipartFile.fromBytes(
          bytes,
          filename: fileName,
          contentType: mimeType != null ? DioMediaType.parse(mimeType) : null,
        ),
      });

      final response = await _dio.post<Map<String, dynamic>>(
        url,
        data: formData,
        cancelToken: cancelToken,
        onSendProgress: (sent, total) {
          if (total > 0 && onProgress != null) {
            final progress = ((sent / total) * 100).round();
            onProgress(progress);
          }
        },
      );

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300 || response.data == null) {
        throw Exception('Upload failed: ${response.statusCode}');
      }

      return UploadFileResponse.fromJson(response.data!);
    } on DioException catch (e) {
      if (e.type == DioExceptionType.cancel) {
        throw Exception('Upload cancelled');
      }
      rethrow;
    }
  }

  /// Generate file URL from file ID
  String makeFileUrl(String fileId) {
    final base = apiBase.endsWith('/') ? apiBase : '$apiBase/';
    final url = '${base}v1/chat/files/$fileId';
    return '$url?platform_api_key=$apiKey';
  }
}

