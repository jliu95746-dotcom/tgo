import 'package:dio/dio.dart';
import 'api_client.dart';

/// Configuration for the TGO platform
class PlatformConfig {
  final String? position;
  final String? themeColor;
  final String? widgetTitle;
  final String? welcomeMessage;
  final String? logoUrl;
  final String? displayMode;

  PlatformConfig({
    this.position,
    this.themeColor,
    this.widgetTitle,
    this.welcomeMessage,
    this.logoUrl,
    this.displayMode,
  });

  factory PlatformConfig.fromJson(Map<String, dynamic> json) {
    return PlatformConfig(
      position: json['position'] as String?,
      themeColor: json['theme_color'] as String?,
      widgetTitle: json['widget_title'] as String?,
      welcomeMessage: json['welcome_message'] as String?,
      logoUrl: json['logo_url'] as String?,
      displayMode: json['display_mode'] as String?,
    );
  }
}

/// Information about the TGO platform
class PlatformInfo {
  final String? id;
  final String? name;
  final String? displayName;
  final PlatformConfig? config;

  PlatformInfo({
    this.id,
    this.name,
    this.displayName,
    this.config,
  });

  factory PlatformInfo.fromJson(Map<String, dynamic> json) {
    return PlatformInfo(
      id: json['id'] as String?,
      name: json['name'] as String?,
      displayName: json['display_name'] as String?,
      config: json['config'] != null
          ? PlatformConfig.fromJson(json['config'] as Map<String, dynamic>)
          : null,
    );
  }
}

/// Service for fetching platform information
class PlatformService {
  final String apiBase;
  final String apiKey;
  final Dio _dio;

  PlatformService({
    required this.apiBase,
    required this.apiKey,
    Dio? dio,
  }) : _dio = dio ?? ApiClient.createDio(apiBase: apiBase, apiKey: apiKey);

  /// Fetch platform information from the API
  Future<PlatformInfo> fetchPlatformInfo() async {
    const url = 'v1/platforms/info';

    try {
      final response = await _dio.get<Map<String, dynamic>>(
        url,
        queryParameters: {'platform_api_key': apiKey},
      );

      if (response.statusCode != 200 || response.data == null) {
        throw Exception(
            'Failed to fetch platform info: ${response.statusCode} ${response.statusMessage}');
      }

      return PlatformInfo.fromJson(response.data!);
    } on DioException catch (e) {
      final errorMessage = _extractDioErrorMessage(e);
      throw Exception('Failed to fetch platform info: $errorMessage');
    } catch (e) {
      throw Exception('Failed to fetch platform info: $e');
    }
  }

  /// Extract meaningful error message from DioException
  String _extractDioErrorMessage(DioException e) {
    // Try to get response data first
    if (e.response?.data != null) {
      final data = e.response!.data;
      if (data is Map && data['message'] != null) {
        return data['message'].toString();
      }
      return data.toString();
    }
    
    // Check for specific error types
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
        return 'Connection timeout - please check your network';
      case DioExceptionType.sendTimeout:
        return 'Send timeout - please check your network';
      case DioExceptionType.receiveTimeout:
        return 'Receive timeout - please check your network';
      case DioExceptionType.connectionError:
        final originalError = e.error?.toString() ?? '';
        if (originalError.contains('Failed host lookup')) {
          return 'Cannot reach server - please check your network connection';
        }
        if (originalError.contains('Connection reset')) {
          return 'Connection reset - please try again';
        }
        return 'Connection error: ${e.message ?? originalError}';
      case DioExceptionType.badResponse:
        return 'Server error: ${e.response?.statusCode} ${e.response?.statusMessage}';
      case DioExceptionType.cancel:
        return 'Request cancelled';
      default:
        return e.message ?? e.error?.toString() ?? 'Unknown network error';
    }
  }
}

