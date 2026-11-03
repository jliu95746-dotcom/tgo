import 'package:flutter/material.dart';
import '../../models/message.dart';
import '../../state/chat_provider.dart';
import '../../utils/time_format.dart';
import '../theme/tgo_theme.dart';
import 'messages/message_bubble.dart';
import 'messages/text_bubble.dart';
import 'messages/image_bubble.dart';
import 'messages/file_bubble.dart';
import 'messages/system_message.dart';

/// Message list widget with smooth animations like major messaging apps
class MessageList extends StatefulWidget {
  final ChatProvider provider;

  const MessageList({
    super.key,
    required this.provider,
  });

  @override
  State<MessageList> createState() => _MessageListState();
}

class _MessageListState extends State<MessageList> {
  final ScrollController _scrollController = ScrollController();
  int _previousMessageCount = 0;
  bool _isAtBottom = true;
  
  // 追踪已经显示过的消息ID，用于判断是否需要播放动画
  final Set<String> _displayedMessageIds = {};

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    widget.provider.addListener(_onProviderChanged);
    _previousMessageCount = widget.provider.messages.length;
    
    // 初始化时，标记所有现有消息为已显示（不需要动画）
    for (final msg in widget.provider.messages) {
      _displayedMessageIds.add(msg.id);
    }
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    widget.provider.removeListener(_onProviderChanged);
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;

    final pos = _scrollController.position;
    
    // 反向列表中，offset 接近 0 表示在视觉底部（最新消息）
    _isAtBottom = pos.pixels <= 50;

    // 加载更多历史：在视觉顶部（即高 offset）附近触发
    final nearTop = pos.pixels >= pos.maxScrollExtent - 100;
    if (nearTop &&
        !widget.provider.state.historyLoading &&
        widget.provider.state.historyHasMore) {
      widget.provider.loadMoreHistory();
    }
  }

  void _onProviderChanged() {
    if (!mounted) return;

    final currentMessages = widget.provider.messages;
    final currentCount = currentMessages.length;
    
    // 新消息到达时的处理
    if (currentCount > _previousMessageCount) {
      // 如果用户在底部，或者是用户自己发送的消息，滚动到底部
      if (_isAtBottom || (currentMessages.isNotEmpty && currentMessages.last.isUser)) {
        _scrollToBottom();
      }
    } else if (currentCount == _previousMessageCount && currentCount > 0) {
      // 流式输出更新：如果用户在底部，保持在底部
      if (_isAtBottom) {
        _scrollToBottom();
      }
    }

    _previousMessageCount = currentCount;
    setState(() {});
  }

  void _scrollToBottom() {
    if (!mounted) return;
    // 反向列表中，底部是 offset 0
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.jumpTo(0);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = TgoThemeProvider.of(context);
    final state = widget.provider.state;
    final messages = state.messages;

    return Container(
      color: theme.bgPrimary,
      child: ListView.builder(
        reverse: true,  // 关键：反向渲染，从底部开始
        controller: _scrollController,
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 8),
        // +1 为顶部状态栏（在反向列表中，它是最后一个 item）
        itemCount: messages.length + 1,
        itemBuilder: (context, index) {
          // 反向列表中，index 0 是最新消息（视觉底部）
          // 最后一个 index 是顶部状态栏（视觉顶部）
          if (index == messages.length) {
            return _buildTopNotice(theme, state);
          }
          // 反转索引：index 0 对应 messages.last（最新消息）
          final messageIndex = messages.length - 1 - index;
          final message = messages[messageIndex];
          
          // 判断这条消息是否需要动画（新消息）
          final isNewMessage = !_displayedMessageIds.contains(message.id);
          if (isNewMessage) {
            _displayedMessageIds.add(message.id);
          }
          
          return _MessageItemAnimated(
            key: ValueKey(message.id),
            message: message,
            theme: theme,
            shouldAnimate: isNewMessage,
            isUser: message.isUser,
            child: _buildMessageItem(context, message, theme),
          );
        },
      ),
    );
  }

  Widget _buildTopNotice(TgoTheme theme, state) {
    if (state.historyLoading) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Center(
          child: SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              valueColor: AlwaysStoppedAnimation<Color>(theme.textMuted),
            ),
          ),
        ),
      );
    }

    if (state.historyError != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Column(
          children: [
            Text(
              'Load history failed',
              style: TextStyle(color: theme.errorColor, fontSize: 12),
            ),
            TextButton(
              onPressed: () => widget.provider.loadMoreHistory(),
              child: Text(
                'Retry',
                style: TextStyle(
                  color: theme.errorColor,
                  fontSize: 12,
                  decoration: TextDecoration.underline,
                ),
              ),
            ),
          ],
        ),
      );
    }

    if (!state.historyHasMore && state.messages.isNotEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Center(
          child: Text(
            'No more messages',
            style: TextStyle(color: theme.textMuted, fontSize: 12),
          ),
        ),
      );
    }

    return const SizedBox.shrink();
  }

  Widget _buildMessageItem(
      BuildContext context, ChatMessage message, TgoTheme theme) {
    // System message
    if (message.isSystemMessage && message.payload is SystemPayload) {
      return SystemMessageWidget(payload: message.payload as SystemPayload);
    }

    final isUser = message.isUser;

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment:
            isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          // Message content
          Row(
            mainAxisAlignment:
                isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
            children: [
              Flexible(
                fit: FlexFit.loose,
                child: _buildMessageContent(context, message, theme),
              ),
            ],
          ),

          // Time
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              TimeFormat.formatMessageTime(message.time),
              style: TextStyle(
                color: theme.textMuted,
                fontSize: 11,
              ),
            ),
          ),

          // Status for user messages
          if (isUser) ...[
            if (message.status == MessageStatus.sending)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(
                  'Sending...',
                  style: TextStyle(
                    color: theme.textMuted,
                    fontSize: 11,
                  ),
                ),
              )
            else if (message.status == MessageStatus.uploading)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      'Uploading ${message.uploadProgress ?? 0}%',
                      style: TextStyle(
                        color: theme.textMuted,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              )
            else if (message.status == MessageStatus.failed)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.error_outline,
                      size: 14,
                      color: theme.errorColor,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'Failed',
                      style: TextStyle(
                        color: theme.errorColor,
                        fontSize: 11,
                      ),
                    ),
                    const SizedBox(width: 8),
                    GestureDetector(
                      onTap: () => widget.provider.retryMessage(message.id),
                      child: Text(
                        'Retry',
                        style: TextStyle(
                          color: theme.primaryColor,
                          fontSize: 11,
                          decoration: TextDecoration.underline,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],

          // Error message from AI
          if (message.errorMessage != null &&
              message.errorMessage!.isNotEmpty &&
              !isUser)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.warning_amber_rounded,
                    size: 14,
                    color: theme.errorColor,
                  ),
                  const SizedBox(width: 4),
                  Flexible(
                    child: Text(
                      message.errorMessage!,
                      style: TextStyle(
                        color: theme.errorColor,
                        fontSize: 12,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildMessageContent(
      BuildContext context, ChatMessage message, TgoTheme theme) {
    final payload = message.payload;
    final isUser = message.isUser;

    // Streaming content
    if (message.isStreaming) {
      return TextBubble(
        content: message.streamData!,
        isUser: false,
        isStreaming: true,
        onSendMessage: widget.provider.sendMessage,
      );
    }

    // AI Loading
    if (payload.type == MessageType.aiLoading) {
      return MessageBubble(
        isUser: false,
        child: const AILoadingDots(),
      );
    }

    // Error message
    if (message.errorMessage != null &&
        message.errorMessage!.isNotEmpty &&
        !isUser &&
        payload is TextPayload) {
      return MessageBubble(
        isUser: false,
        isError: true,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.error_outline, size: 16, color: theme.errorColor),
            const SizedBox(width: 6),
            Flexible(child: Text(message.errorMessage!)),
          ],
        ),
      );
    }

    // Text message
    if (payload is TextPayload) {
      return TextBubble(
        content: payload.content,
        isUser: isUser,
        onSendMessage: widget.provider.sendMessage,
      );
    }

    // Image message
    if (payload is ImagePayload) {
      return ImageBubble(
        url: payload.url,
        width: payload.width,
        height: payload.height,
      );
    }

    // File message
    if (payload is FilePayload) {
      return FileBubble(
        url: payload.url,
        name: payload.name,
        size: payload.size,
      );
    }

    // Mixed message
    if (payload is MixedPayload) {
      return Column(
        crossAxisAlignment:
            isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          if (payload.content.isNotEmpty)
            TextBubble(
              content: payload.content,
              isUser: isUser,
              onSendMessage: widget.provider.sendMessage,
            ),
          if (payload.images.isNotEmpty)
            Padding(
              padding: EdgeInsets.only(top: payload.content.isNotEmpty ? 8 : 0),
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: payload.images
                    .map((img) => ImageBubble(
                          url: img.url,
                          width: img.width,
                          height: img.height,
                        ))
                    .toList(),
              ),
            ),
          if (payload.file != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: FileBubble(
                url: payload.file!.url,
                name: payload.file!.name,
                size: payload.file!.size,
              ),
            ),
        ],
      );
    }

    // Fallback
    return MessageBubble(
      isUser: isUser,
      child: const Text('[Message]'),
    );
  }
}

/// 消息动画组件 - 像微信、iMessage一样的自然动画
class _MessageItemAnimated extends StatefulWidget {
  final ChatMessage message;
  final TgoTheme theme;
  final bool shouldAnimate;
  final bool isUser;
  final Widget child;

  const _MessageItemAnimated({
    super.key,
    required this.message,
    required this.theme,
    required this.shouldAnimate,
    required this.isUser,
    required this.child,
  });

  @override
  State<_MessageItemAnimated> createState() => _MessageItemAnimatedState();
}

class _MessageItemAnimatedState extends State<_MessageItemAnimated>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  late Animation<double> _fadeAnimation;
  late Animation<double> _scaleAnimation;
  late Animation<Offset> _slideAnimation;

  @override
  void initState() {
    super.initState();
    
    // 动画控制器：持续时间350ms，足够流畅但不会太慢
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 350),
    );

    // 淡入动画：从0到1，使用easeOut曲线
    _fadeAnimation = CurvedAnimation(
      parent: _controller,
      curve: Curves.easeOut,
    );

    // 缩放动画：从0.85到1.0，带有轻微的弹跳效果
    // 使用elasticOut会有过度弹跳，所以用easeOutBack获得更自然的效果
    _scaleAnimation = Tween<double>(
      begin: 0.85,
      end: 1.0,
    ).animate(CurvedAnimation(
      parent: _controller,
      curve: Curves.easeOutBack,
    ));

    // 滑动动画：根据消息方向决定滑动方向
    // 用户消息从右边滑入，客服消息从左边滑入
    _slideAnimation = Tween<Offset>(
      begin: Offset(widget.isUser ? 0.15 : -0.15, 0.1),
      end: Offset.zero,
    ).animate(CurvedAnimation(
      parent: _controller,
      curve: Curves.easeOutCubic,
    ));

    // 只有新消息才播放动画
    if (widget.shouldAnimate) {
      _controller.forward();
    } else {
      // 已存在的消息直接显示完成状态
      _controller.value = 1.0;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // 如果不需要动画，直接返回子组件
    if (!widget.shouldAnimate && _controller.value == 1.0) {
      return widget.child;
    }

    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return Opacity(
          opacity: _fadeAnimation.value,
          child: Transform.translate(
            offset: Offset(
              _slideAnimation.value.dx * MediaQuery.of(context).size.width * 0.3,
              _slideAnimation.value.dy * 30,
            ),
            child: Transform.scale(
              scale: _scaleAnimation.value,
              alignment: widget.isUser ? Alignment.centerRight : Alignment.centerLeft,
              child: child,
            ),
          ),
        );
      },
      child: widget.child,
    );
  }
}
