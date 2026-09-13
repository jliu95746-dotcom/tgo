import React from 'react';
import MarkdownContent from '../MarkdownContent';
import { MessagePayloadType, type Message } from '@/types';

/**
 * Streaming cursor animation component
 */
const StreamingCursor: React.FC<{ isWhite?: boolean }> = ({ isWhite }) => (
  <span
    className={`inline-block w-2 h-4 ml-0.5 animate-pulse ${
      isWhite ? 'bg-white/80' : 'bg-gray-500 dark:bg-gray-400'
    }`}
    style={{ verticalAlign: 'text-bottom' }}
  />
);

/**
 * TextMessage renders plain text messages.
 */
export interface MessageComponentProps {
  message: Message;
  isStaff: boolean; // true for staff (right), false for visitor (left)
  /** 发送消息回调（用于 Widget 中的 msg:// 协议） */
  onSendMessage?: (message: string) => void;
}

/** Strip ```spec fences from text to prevent leaking raw JSON patches if fences reach TextMessage. */
function stripSpecFences(text: string): string {
  return text.replace(/```spec[\s\S]*?```/g, '').replace(/```spec[\s\S]*/g, '').trim();
}

const TextMessage: React.FC<MessageComponentProps> = ({ message, isStaff, onSendMessage }) => {
  const typedPayload = message.payload as any | undefined;
  // Prefer message.content for streaming updates; fall back to payload content
  const rawContent: string =
    (typeof message.content === 'string' && message.content.length > 0)
      ? message.content
      : ((typedPayload?.type === MessagePayloadType.TEXT && typeof typedPayload?.content === 'string')
          ? typedPayload.content
          : '');
  // Safety net: strip spec fences if they somehow leak into TextMessage rendering
  const textContent = rawContent.includes('```spec') ? stripSpecFences(rawContent) : rawContent;

  // Render streamed markdown only when has_stream_data flag exists
  const hasStreamData = Boolean(message.metadata?.has_stream_data);
  const shouldRenderMarkdown = hasStreamData;

  // Check if message is still streaming (AI response in progress)
  const isStreaming = Boolean(message.metadata?.is_streaming);

  const hasLink = (message as any).hasLink;


  if (isStaff) {
    return (
      <div className="inline-block bg-blue-500 dark:bg-blue-600 text-white p-3 rounded-lg rounded-tr-none shadow-sm overflow-hidden">
        {shouldRenderMarkdown ? (
          <>
            <MarkdownContent content={textContent} className="text-sm markdown-white" onSendMessage={onSendMessage} />
            {isStreaming && <StreamingCursor isWhite />}
          </>
        ) : (
          <p className="text-sm break-words">
            {hasLink ? (
              <>
                {textContent.split('耳机连接指南')[0]}
                <a href="#" className="underline">耳机连接指南</a>
              </>
            ) : (
              textContent
            )}
            {isStreaming && <StreamingCursor isWhite />}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="inline-block bg-white dark:bg-gray-700 p-3 rounded-lg rounded-tl-none shadow-sm border border-gray-100 dark:border-gray-600 overflow-hidden">
      {shouldRenderMarkdown ? (
        <>
          <MarkdownContent content={textContent} className="text-sm dark:text-gray-200" onSendMessage={onSendMessage} />
          {isStreaming && <StreamingCursor />}
        </>
      ) : (
        <p className="text-sm text-gray-800 dark:text-gray-200 break-words">
          {textContent}
          {isStreaming && <StreamingCursor />}
        </p>
      )}
    </div>
  );
};

export default TextMessage;

