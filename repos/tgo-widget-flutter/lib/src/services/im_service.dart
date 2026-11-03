import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:wukong_easy_sdk/wukong_easy_sdk.dart';
import '../tgo_widget_impl.dart';

/// IM connection status
typedef IMStatus = ConnectionStatus;

/// Callback types
typedef MessageCallback = void Function(Message message);
typedef StatusCallback = void Function(IMStatus status);
typedef CustomEventCallback = void Function(Map<String, dynamic> event);

/// Service for WuKongIM connection and messaging
class IMService {
  final String apiBase;
  final Dio _dio;

  WuKongEasySDK? _sdk;
  String? _uid;
  String? _target;

  bool _initialized = false;
  bool _connected = false;

  // Event listeners
  final Set<MessageCallback> _messageListeners = {};
  final Set<StatusCallback> _statusListeners = {};
  final Set<CustomEventCallback> _customEventListeners = {};

  IMService({
    required this.apiBase,
    Dio? dio,
  }) : _dio = dio ??
            Dio(BaseOptions(
              connectTimeout: const Duration(seconds: 10),
              receiveTimeout: const Duration(seconds: 10),
            ));

  /// Whether the service is initialized
  bool get isInitialized => _initialized;

  /// Whether connected to IM server
  bool get isConnected => _connected;

  /// Current user UID
  String? get uid => _uid;

  /// Initialize the IM service
  Future<void> init({
    required String uid,
    required String token,
    required String target,
    int channelType = 251,
  }) async {
    _uid = uid;
    _target = target;

    // Get WebSocket address from route API
    final wsAddr = await _fetchRouteWsAddr(uid);
    if (wsAddr == null || wsAddr.isEmpty) {
      throw Exception('Failed to get WebSocket address from route API');
    }

    // Initialize WuKongIM SDK with config
    _sdk = WuKongEasySDK.getInstance();
    final config = WuKongConfig(
      serverUrl: wsAddr,
      uid: uid,
      token: token,
    );
    await _sdk!.init(config);

    // Setup event listeners
    _setupEventListeners();

    _initialized = true;
  }

  /// Setup internal event listeners
  void _setupEventListeners() {
    if (_sdk == null) return;

    // Connection events
    _sdk!.addEventListener(WuKongEvent.connect, (_) {
      _connected = true;
      _emitStatus(ConnectionStatus.connected);
    });

    _sdk!.addEventListener(WuKongEvent.disconnect, (_) {
      _connected = false;
      _emitStatus(ConnectionStatus.disconnected);
    });

    _sdk!.addEventListener(WuKongEvent.error, (_) {
      _emitStatus(ConnectionStatus.error);
    });

    // Message events
    _sdk!.addEventListener(WuKongEvent.message, (data) {
      if (data == null) return;
      if (data is Message) {
        _emitMessage(data);
      }
    });

    // Custom events (streaming etc.)
    _sdk!.addEventListener(WuKongEvent.customEvent, (data) {
      if (data == null) return;

      if (data is Map) {
        // Convert Map to Map<String, dynamic> safely
        final map = data.map((key, value) => MapEntry(key.toString(), value));
        _emitCustomEvent(map);
      } else {
        // Try to extract fields from the event object (it's likely an EventNotification instance)
        try {
          final dynamic event = data;
          final map = <String, dynamic>{
            'id': event.id?.toString(),
            'type': event.type?.toString(),
            'data': event.data,
            'timestamp': event.timestamp,
          };
          _emitCustomEvent(map);
        } catch (_) {
          debugPrint('[IMService] Failed to parse custom event');
        }
      }
    });
  }

  /// Connect to IM server
  Future<void> connect() async {
    if (!_initialized || _sdk == null) {
      throw Exception('IMService not initialized. Call init() first.');
    }

    _emitStatus(ConnectionStatus.connecting);

    try {
      await _sdk!.connect();
    } catch (_) {
      debugPrint('[IMService] Connect failed');
      _emitStatus(ConnectionStatus.error);
      rethrow;
    }
  }

  /// Disconnect from IM server
  Future<void> disconnect() async {
    if (_sdk != null) {
      _sdk!.dispose();
      _connected = false;
      _emitStatus(ConnectionStatus.disconnected);
    }
  }

  /// Send a text message
  Future<SendResult> sendText(String text, {String? clientMsgNo}) async {
    if (!_initialized || _sdk == null || _target == null) {
      throw Exception('IMService not ready');
    }

    final payload = {'type': 1, 'content': text};
    return sendPayload(payload, clientMsgNo: clientMsgNo);
  }

  /// Send a message with custom payload
  Future<SendResult> sendPayload(
    Map<String, dynamic> payload, {
    String? clientMsgNo,
  }) async {
    if (!_initialized || _sdk == null || _target == null) {
      throw Exception('IMService not ready');
    }
    const customerServiceType = WuKongChannelType(251);
    return _sdk!.send(
      channelId: _target!,
      channelType: customerServiceType,
      payload: payload,
      clientMsgNo: clientMsgNo,
    );
  }

  /// Fetch WebSocket address from route API
  Future<String?> _fetchRouteWsAddr(String uid) async {
    try {
      final base = apiBase.endsWith('/') ? apiBase : '$apiBase/';
      final url = '${base}v1/wukongim/route?uid=$uid';

      final response = await _dio.get<Map<String, dynamic>>(url);

      final statusCode = response.statusCode ?? 0;
      if (statusCode < 200 || statusCode >= 300 || response.data == null) {
        throw Exception('Route fetch failed: ${response.statusCode}');
      }

      final data = response.data!;

      // Priority: wss_addr > ws_addr
      String? addr = data['wss_addr'] as String?;
      if (addr == null || addr.isEmpty) {
        addr = data['ws_addr'] as String?;
      }

      if (addr != null && addr.startsWith('http')) {
        addr = addr.replaceFirst(RegExp(r'^http'), 'ws');
      }

      return addr;
    } catch (_) {
      debugPrint('[IMService] Route fetch failed');
      rethrow;
    }
  }

  /// Add message listener
  void addMessageListener(MessageCallback callback) {
    _messageListeners.add(callback);
  }

  /// Remove message listener
  void removeMessageListener(MessageCallback callback) {
    _messageListeners.remove(callback);
  }

  /// Add status listener
  void addStatusListener(StatusCallback callback) {
    _statusListeners.add(callback);
  }

  /// Remove status listener
  void removeStatusListener(StatusCallback callback) {
    _statusListeners.remove(callback);
  }

  /// Add custom event listener
  void addCustomEventListener(CustomEventCallback callback) {
    _customEventListeners.add(callback);
  }

  /// Remove custom event listener
  void removeCustomEventListener(CustomEventCallback callback) {
    _customEventListeners.remove(callback);
  }

  /// Emit message to listeners
  void _emitMessage(Message message) {
    for (final listener in _messageListeners) {
      try {
        listener(message);
      } catch (_) {
        debugPrint('[IMService] Message listener failed');
      }
    }
  }

  /// Emit status to listeners
  void _emitStatus(IMStatus status) {
    for (final listener in _statusListeners) {
      try {
        listener(status);
      } catch (_) {
        debugPrint('[IMService] Status listener failed');
      }
    }
  }

  /// Emit custom event to listeners
  void _emitCustomEvent(Map<String, dynamic> event) {
    for (final listener in _customEventListeners) {
      try {
        listener(event);
      } catch (_) {
        debugPrint('[IMService] Custom event listener failed');
      }
    }
  }
}

