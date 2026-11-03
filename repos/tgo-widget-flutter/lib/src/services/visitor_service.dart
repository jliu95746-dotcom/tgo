import 'dart:io';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import '../models/visitor.dart';
import '../config/visitor_info.dart';
import '../utils/storage.dart';
import 'api_client.dart';

/// Service for visitor registration and management
class VisitorService {
  final String apiBase;
  final String apiKey;
  final Dio _dio;

  CachedVisitor? _cachedVisitor;
  VisitorInfo? _currentVisitorInfo;

  VisitorService({
    required this.apiBase,
    required this.apiKey,
    Dio? dio,
  }) : _dio = dio ?? ApiClient.createDio(apiBase: apiBase, apiKey: apiKey);

  /// Get cached visitor or null
  CachedVisitor? get cachedVisitor => _cachedVisitor;

  /// Check if visitor is registered
  bool get isRegistered => _cachedVisitor != null;

  /// Get visitor UID for IM connection
  String? get visitorUid => _cachedVisitor?.imUid;

  /// Get IM token
  String? get imToken => _cachedVisitor?.imToken;

  /// Get channel ID
  String? get channelId => _cachedVisitor?.channelId;

  /// Get channel type
  int? get channelType => _cachedVisitor?.channelType;

  /// Set current visitor info (from config)
  void updateVisitorInfo(VisitorInfo? info) {
    _currentVisitorInfo = info;
  }

  /// Get the correct cache key based on current visitor info
  String _getCacheKey() {
    return VisitorCacheInfo(
      apiBase: apiBase,
      apiKey: apiKey,
      platformOpenId: _currentVisitorInfo?.platformOpenId,
    ).cacheKey;
  }

  /// Load cached visitor from local storage
  Future<CachedVisitor?> loadCachedVisitor() async {
    final cacheKey = _getCacheKey();
    final json = await TgoStorage.getJson(cacheKey);

    if (json != null) {
      try {
        _cachedVisitor = CachedVisitor.fromJson(json);
        return _cachedVisitor;
      } catch (_) {
        await TgoStorage.remove(cacheKey);
      }
    }
    return null;
  }

  /// Save visitor to local storage
  Future<void> _saveCachedVisitor(CachedVisitor visitor) async {
    final cacheKey = _getCacheKey();
    await TgoStorage.setJson(cacheKey, visitor.toJson());
    _cachedVisitor = visitor;
  }

  /// Register a new visitor
  Future<VisitorRegisterResponse> register({
    VisitorSystemInfo? systemInfo,
    String? timezone,
  }) async {
    const url = 'v1/visitors/register';

    // Collect system info
    final sysInfo = systemInfo ?? _collectSystemInfo();
    final tz = timezone ?? DateTime.now().timeZoneName;

    final request = VisitorRegisterRequest(
      platformApiKey: apiKey,
      platformOpenId: _currentVisitorInfo?.platformOpenId,
      name: _currentVisitorInfo?.name,
      nickname: _currentVisitorInfo?.nickname,
      avatarUrl: _currentVisitorInfo?.avatarUrl,
      phoneNumber: _currentVisitorInfo?.phoneNumber,
      email: _currentVisitorInfo?.email,
      company: _currentVisitorInfo?.company,
      jobTitle: _currentVisitorInfo?.jobTitle,
      customAttributes: _currentVisitorInfo?.customAttributes,
      systemInfo: sysInfo,
      timezone: tz,
    );

    try {
      final response = await _dio.post<Map<String, dynamic>>(
        url,
        data: request.toJson(),
      );

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300 || response.data == null) {
        throw Exception(
            'Registration failed: ${response.statusCode} ${response.statusMessage}');
      }

      final visitorResponse = VisitorRegisterResponse.fromJson(response.data!);

      // Cache the visitor
      final cached = CachedVisitor.fromResponse(
        apiBase,
        apiKey,
        visitorResponse,
      );
      await _saveCachedVisitor(cached);

      return visitorResponse;
    } on DioException catch (e) {
      final errorMessage = _extractDioErrorMessage(e);
      throw Exception('Registration failed: $errorMessage');
    } catch (e) {
      throw Exception('Registration failed: $e');
    }
  }

  /// Extract meaningful error message from DioException
  String _extractDioErrorMessage(DioException e) {
    if (e.response?.data != null) {
      final data = e.response!.data;
      if (data is Map && data['message'] != null) {
        return data['message'].toString();
      }
      return data.toString();
    }
    
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
        return 'Connection error: ${e.message ?? originalError}';
      case DioExceptionType.badResponse:
        return 'Server error: ${e.response?.statusCode} ${e.response?.statusMessage}';
      case DioExceptionType.cancel:
        return 'Request cancelled';
      default:
        return e.message ?? e.error?.toString() ?? 'Unknown network error';
    }
  }

  /// Ensure visitor is registered (load cache or register)
  Future<CachedVisitor> ensureRegistered() async {
    // Try to load from cache first
    var cached = await loadCachedVisitor();
    if (cached != null) {
      return cached;
    }

    // Register new visitor
    await register();

    cached = _cachedVisitor;
    if (cached == null) {
      throw Exception('Failed to register visitor');
    }

    return cached;
  }

  /// Clear cached visitor
  Future<void> clearCache() async {
    final cacheKey = _getCacheKey();
    await TgoStorage.remove(cacheKey);
    _cachedVisitor = null;
  }

  /// Collect system information
  VisitorSystemInfo _collectSystemInfo() {
    String? os;
    String? deviceType;

    if (kIsWeb) {
      os = 'Web';
      deviceType = 'browser';
    } else {
      try {
        os = Platform.operatingSystem;
        if (Platform.isIOS || Platform.isAndroid) {
          deviceType = 'mobile';
        } else if (Platform.isMacOS || Platform.isWindows || Platform.isLinux) {
          deviceType = 'desktop';
        }
      } catch (e) {
        os = 'unknown';
      }
    }

    return VisitorSystemInfo(
      operatingSystem: os,
      deviceType: deviceType,
      sourceDetail: 'Flutter SDK',
    );
  }
}
