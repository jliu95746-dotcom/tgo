import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:markdown_widget/markdown_widget.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../theme/tgo_theme.dart';
import 'message_bubble.dart';
import '../tgo_widgets/tgo_widgets.dart';

/// Text message bubble with Markdown support and TGO UI Widget support
class TextBubble extends StatelessWidget {
  final String content;
  final bool isUser;
  final bool isStreaming;
  final SendMessageCallback? onSendMessage;
  final ActionCallback? onAction;
  final ValueChanged<String>? onCopySuccess;

  const TextBubble({
    super.key,
    required this.content,
    required this.isUser,
    this.isStreaming = false,
    this.onSendMessage,
    this.onAction,
    this.onCopySuccess,
  });

  @override
  Widget build(BuildContext context) {
    final theme = TgoThemeProvider.of(context);
    final isDark = Theme.of(context).brightness == Brightness.dark;

    final markdownConfig = (isDark
            ? MarkdownConfig.darkConfig
            : MarkdownConfig.defaultConfig)
        .copy(configs: [
      PConfig(
        textStyle: TextStyle(
          color: theme.agentBubbleText,
          fontSize: 15,
          height: 1.4,
        ),
      ),
      CodeConfig(
        style: TextStyle(
          backgroundColor: theme.bgSecondary,
          color: theme.primaryColor,
          fontSize: 13,
        ),
      ),
      PreConfig(
        decoration: BoxDecoration(
          color: theme.bgSecondary,
          borderRadius: BorderRadius.circular(8),
        ),
        builder: (String code, String language) {
          final trimmedLanguage = language.trim().toLowerCase();
          final isWidget = trimmedLanguage == 'tgo-ui-widget' ||
              trimmedLanguage == 'language-tgo-ui-widget' ||
              trimmedLanguage == 'widget';

          if (isWidget) {
            try {
              String jsonStr = code.trim();
              // Handle potential escaped quotes if the AI output is messy
              if (jsonStr.contains('\\"')) {
                jsonStr = jsonStr.replaceAll('\\"', '"');
              }

              final json = jsonDecode(jsonStr);
              if (json is Map<String, dynamic>) {
                return WidgetRenderer(
                  data: WidgetData.fromJson(json),
                  onSendMessage: onSendMessage,
                  onAction: onAction,
                  onCopySuccess: onCopySuccess,
                );
              }
            } catch (_) {
              // Render the original code block without logging customer content.
            }
          }

          // Default rendering for code blocks if not our widget or parsing failed
          return Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            margin: const EdgeInsets.symmetric(vertical: 8),
            decoration: BoxDecoration(
              color: theme.bgSecondary,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: theme.borderPrimary),
            ),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(
                code,
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 13,
                  color: theme.textPrimary,
                ),
              ),
            ),
          );
        },
      ),
      LinkConfig(
        style: TextStyle(
          color: theme.primaryColor,
          decoration: TextDecoration.underline,
        ),
        onTap: (url) {
          launchUrl(Uri.parse(url));
        },
      ),
    ]);

    final contentWidget = isUser
        ? Text(
            content,
            style: TextStyle(
              color: theme.userBubbleText,
              fontSize: 15,
              height: 1.4,
            ),
          )
        : MarkdownBlock(
            data: content,
            config: markdownConfig,
          );

    return MessageBubble(
      isUser: isUser,
      child: isStreaming
          ? Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Flexible(child: contentWidget),
                const BlinkingCursor(),
              ],
            )
          : contentWidget,
    );
  }
}
