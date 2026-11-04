import 'package:dio/dio.dart';

/// 统一的 API 客户端封装
class ApiClient {
  static Dio createDio({
    required String apiBase,
    String? apiKey,
    Duration connectTimeout = const Duration(seconds: 15),
    Duration receiveTimeout = const Duration(seconds: 30),
  }) {
    final dio = Dio(BaseOptions(
      baseUrl: apiBase.endsWith('/') ? apiBase : '$apiBase/',
      connectTimeout: connectTimeout,
      receiveTimeout: receiveTimeout,
      headers: {
        if (apiKey != null) 'X-Platform-API-Key': apiKey,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
    ));

    return dio;
  }
}

