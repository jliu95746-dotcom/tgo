import React from 'react';
import { MessagePayloadType, type Message } from '@/types';
import MarkdownContent from '../MarkdownContent';
import { uploadChatImageWithProgress } from '@/services/chatUploadApi';
import { toAbsoluteApiUrl } from '@/utils/url';

import { useChatStore } from '@/stores/chatStore';
import { useToast } from '@/hooks/useToast';
import { showApiError } from '@/utils/toastHelpers';
import { formatBytes } from '@/utils/format';
import { getFileIcon } from '@/utils/fileIcons';

import Lightbox from 'yet-another-react-lightbox';
import Zoom from 'yet-another-react-lightbox/plugins/zoom';
import 'yet-another-react-lightbox/styles.css';

/**
 * RichTextMessage renders text + images grid and optional attached file.
 */
export interface MessageComponentProps {
  message: Message;
  isStaff: boolean;
  /** 发送消息回调（用于 Widget 中的 msg:// 协议） */
  onSendMessage?: (message: string) => void;
}

const RichTextMessage: React.FC<MessageComponentProps> = ({ message, isStaff, onSendMessage }) => {
  const typedPayload = message.payload as any | undefined;
  const isStream = Boolean(message.metadata?.has_stream_data);
  // Prefer live message.content when streaming; otherwise fall back to payload content
  const richContent: string = isStream
    ? (message.content || (typeof typedPayload?.content === 'string' ? typedPayload.content : ''))
    : ((typedPayload?.type === MessagePayloadType.RICH_TEXT && typeof typedPayload?.content === 'string')
        ? typedPayload.content
        : (message.content || ''));

  // Images array (max 9)
  type RichImg = { url: string; width?: number; height?: number; upload_status?: string; upload_progress?: number; file?: File; preview_url?: string };
  const richImages: RichImg[] = React.useMemo(() => {
    let src: any[] = [];
    if (typedPayload?.type === MessagePayloadType.RICH_TEXT) {
      const imgsMaybe: any = (typedPayload as any).images;
      if (Array.isArray(imgsMaybe)) {
        src = imgsMaybe;
      } else if (typeof imgsMaybe === 'string') {
        try {
          const parsed = JSON.parse(imgsMaybe);
          if (Array.isArray(parsed)) src = parsed;
        } catch {}
      }
    }
    if (!src.length && Array.isArray(message.metadata?.images)) {
      src = message.metadata!.images as any[];
    }
    const arr = src.slice(0, 9);
    return arr.map((img: any) => ({
      url: (img?.url || img?.image_url || img?.file_url || img?.preview_url || '') as string,
      width: typeof img?.width === 'number' ? img.width : (typeof img?.image_width === 'number' ? img.image_width : undefined),
      height: typeof img?.height === 'number' ? img.height : (typeof img?.image_height === 'number' ? img.image_height : undefined),
      upload_status: img?.upload_status,
      upload_progress: typeof img?.upload_progress === 'number' ? img.upload_progress : undefined,
      file: img?.file as File | undefined,
      preview_url: typeof img?.preview_url === 'string' ? img.preview_url : undefined,
    }));
  }, [typedPayload, message.metadata]);

  const richSlides = React.useMemo(() => richImages.filter(im => im.url).map(im => ({ src: im.url })), [richImages]);
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const [previewIndex, setPreviewIndex] = React.useState(0);

  // Attached file (optional)
  type RichFile = { url: string; name: string; size?: number; upload_status?: string; upload_progress?: number; file?: File };
  const richFile: RichFile | null = React.useMemo(() => {
    if (typedPayload?.type === MessagePayloadType.RICH_TEXT) {
      const fileMaybe: any = (typedPayload as any).file;
      if (fileMaybe && typeof fileMaybe === 'object' && (fileMaybe.url || fileMaybe.file_url)) {
        return {
          url: (fileMaybe.url || fileMaybe.file_url) as string,
          name: fileMaybe.name || fileMaybe.file_name || '未命名文件',
          size: typeof fileMaybe.size === 'number' ? fileMaybe.size : (typeof fileMaybe.file_size === 'number' ? fileMaybe.file_size : undefined),
        } as any;
      }
    }
    if (message.metadata?.file_url || message.metadata?.file_name) {
      return {
        url: message.metadata.file_url || '',
        name: message.metadata.file_name || '未命名文件',
        size: typeof message.metadata.file_size === 'number' ? message.metadata.file_size : undefined,
        upload_status: message.metadata.upload_status,
        upload_progress: typeof message.metadata.upload_progress === 'number' ? message.metadata.upload_progress : undefined,
        file: message.metadata.file as File | undefined,
      };
    }
    return null;
  }, [typedPayload, message.metadata]);

  // Layout measurement for text vs grid width
  const rtTextRef = React.useRef<HTMLDivElement | null>(null);
  const rtGridRef = React.useRef<HTMLDivElement | null>(null);
  const [rtWidth, setRtWidth] = React.useState<number | undefined>(undefined);

  const recalcRtWidth = React.useCallback(() => {
    const imgCount = richImages.length;
    const textW = rtTextRef.current ? rtTextRef.current.offsetWidth : 0;
    // Prefer measured grid width; fallback to formula if not measurable yet
    let gridW = rtGridRef.current ? rtGridRef.current.scrollWidth : 0;
    if (!gridW && imgCount > 0) {
      const cols = Math.min(3, imgCount);
      const thumb = 100; // w-[100px]
      const border = 2; // 1px left + 1px right
      const gap = 8; // Tailwind gap-2
      gridW = cols * (thumb + border) + (cols - 1) * gap;
    }
    const max = Math.max(textW, gridW);
    if (!rtWidth || Math.abs(max - rtWidth) > 1) setRtWidth(max);
  }, [richImages.length, rtWidth]);

  React.useEffect(() => {
    recalcRtWidth();
    const onResize = () => recalcRtWidth();
    window.addEventListener('resize', onResize);
    const imgs = rtGridRef.current?.querySelectorAll('img') || [];
    imgs.forEach((img) => img.addEventListener('load', recalcRtWidth));
    return () => {
      window.removeEventListener('resize', onResize);
      imgs.forEach((img) => img.removeEventListener('load', recalcRtWidth));
    };
  }, [recalcRtWidth, richContent, richImages.length]);

  const imgCount = richImages.length;
  const gridColsClass = imgCount === 1 ? 'grid-cols-1' : imgCount === 2 ? 'grid-cols-2' : 'grid-cols-3';
  const getGridItemClass = (idx: number): string => {
    if (imgCount >= 3) {
      const remainder = imgCount % 3;
      if (remainder === 1 && idx === imgCount - 1) return 'col-start-2';
    }
    return '';
  };

  // Auto-send when all images finished uploading (WS)
  const updateMessageByClientMsgNo = useChatStore(state => state.updateMessageByClientMsgNo);

  const { showToast } = useToast();





  // Retry single image upload at index
  const retryRichImageUpload = React.useCallback(async (idx: number) => {
    const images: any[] = Array.isArray(message.metadata?.images) ? (message.metadata!.images as any[]) : [];
    const target = images[idx];
    if (!target || target.upload_status !== 'error') return;
    const file: File | undefined = target.file as File | undefined;
    if (!file || !message.channelId || typeof message.channelType !== 'number') return;
    const clientKey = message.clientMsgNo || message.id;

    // Mark as uploading
    const uploadingImages = images.map((img, i) => i === idx ? { ...img, upload_status: 'uploading', upload_progress: 0 } : img);
    updateMessageByClientMsgNo(clientKey, { metadata: { images: uploadingImages } });

    try {
      await uploadChatImageWithProgress(file, message.channelId, message.channelType, {
        onProgress: (p) => {
          const next = uploadingImages.map((img, i) => i === idx ? { ...img, upload_progress: p.progress, upload_status: p.status } : img);
          updateMessageByClientMsgNo(clientKey, { metadata: { images: next } });
        }
      }).then((res) => {
        const url = toAbsoluteApiUrl(res.file_url);
        const completed = uploadingImages.map((img, i) => i === idx ? { ...img, upload_progress: 100, upload_status: 'completed', url } : img);
        updateMessageByClientMsgNo(clientKey, { metadata: { images: completed } });
      }).catch((err) => {
        const failed = uploadingImages.map((img, i) => i === idx ? { ...img, upload_status: 'error' } : img);
        updateMessageByClientMsgNo(clientKey, { metadata: { images: failed } });
        showApiError?.(showToast, err);
      });
    } finally {
      // No auto-send here; sending is coordinated by MessageInput to avoid duplicates
    }
  }, [message.metadata, message.clientMsgNo, message.id, message.channelId, message.channelType, updateMessageByClientMsgNo, showToast]);

  // Rich file rendering (optional)
  const renderRichFile = () => {
    if (!richFile) return null;
    const boxClass = isStaff
      ? 'flex items-center space-x-3 rounded-md border border-blue-200 dark:border-blue-700 bg-white dark:bg-gray-800 px-3 py-2 max-w-xs self-end'
      : 'mt-2 flex items-center space-x-3 rounded-md border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 max-w-xs';
    return (
      <div className={boxClass}>
        {getFileIcon(richFile.name, 'w-5 h-5 flex-shrink-0')}
        <div className="flex-1 min-w-0">
          <p className="text-sm text-gray-800 dark:text-gray-200 truncate">{richFile.name}</p>
          {richFile.size && <p className="text-xs text-gray-500 dark:text-gray-400">{formatBytes(richFile.size)}</p>}
        </div>
        {richFile.upload_status === 'uploading' && (
          <span className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
            {typeof richFile.upload_progress === 'number' ? `${richFile.upload_progress}%` : '上传中'}
          </span>
        )}
        {richFile.upload_status === 'error' && (
          <span className="text-xs text-red-500 dark:text-red-400 flex-shrink-0">上传失败</span>
        )}
        {richFile.url && richFile.upload_status !== 'uploading' && (
          <a
            href={richFile.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-500 dark:text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 flex-shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            打开
          </a>
        )}
      </div>
    );
  };

  const hasStreamData = Boolean(message.metadata?.has_stream_data);
  const shouldRenderMarkdown = hasStreamData;

  const textBubble = isStaff ? (
    <div ref={rtTextRef} className="inline-block bg-blue-500 dark:bg-blue-600 text-white p-3 rounded-lg rounded-tr-none shadow-sm">
      {shouldRenderMarkdown ? (
        <MarkdownContent content={richContent} className="text-sm markdown-white" onSendMessage={onSendMessage} />
      ) : (
        <p className="text-sm">{richContent}</p>
      )}
    </div>
  ) : (
    <div ref={rtTextRef} className="inline-block bg-white dark:bg-gray-700 p-3 rounded-lg rounded-tl-none shadow-sm border border-gray-100 dark:border-gray-600">
      {shouldRenderMarkdown ? (
        <MarkdownContent content={richContent} className="text-sm dark:text-gray-200" onSendMessage={onSendMessage} />
      ) : (
        <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{richContent}</p>
      )}
    </div>
  );

  const gridBoxClass = isStaff ? `grid ${  gridColsClass  } gap-2 w-fit self-end` : `mt-2 grid ${  gridColsClass  } gap-2 w-fit`;

  return (
    <>
      <div style={rtWidth ? { width: `${rtWidth}px` } : undefined} className={isStaff ? 'space-y-2 flex flex-col items-end' : 'flex flex-col'}>
        {textBubble}
        {richImages.length > 0 && (
          <div ref={rtGridRef} className={gridBoxClass}>
            {richImages.map((im, idx) => (
              <div
                key={idx}
                className={
                  `${(isStaff
                    ? 'relative rounded-md overflow-hidden border border-blue-200 dark:border-blue-700 bg-white dark:bg-gray-800 '
                    : 'relative rounded-md overflow-hidden border border-gray-100 dark:border-gray-600 bg-white dark:bg-gray-800 '
                  ) +
                  (im.upload_status === 'error'
                    ? 'cursor-pointer hover:opacity-95'
                    : im.url && im.upload_status !== 'uploading'
                    ? 'cursor-zoom-in hover:opacity-95'
                    : 'cursor-default')
                  } ${  getGridItemClass(idx)}`
                }
                role="button"
                aria-disabled={im.upload_status === 'uploading'}
                onClick={() => {
                  if (im.upload_status === 'error') {
                    retryRichImageUpload(idx);
                  } else if (im.url && im.upload_status !== 'uploading') {
                    setPreviewIndex(idx); setPreviewOpen(true);
                  }
                }}
              >
                {im.url ? (
                  <img
                    src={im.url}
                    alt={`图片${idx + 1}`}
                    className="w-[100px] h-[100px] object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-[100px] h-[100px] bg-gray-100" />
                )}
                {im.upload_status === 'uploading' && (
                  <div className="absolute inset-0 bg-black/40 flex items-center justify-center text-white text-xs">
                    {typeof im.upload_progress === 'number' ? `${im.upload_progress}%` : '上传中'}
                  </div>
                )}
                {im.upload_status === 'error' && (
                  <div className="absolute inset-0 bg-red-500/20 flex items-center justify-center text-red-700 text-xs text-center px-1 leading-5">
                    上传失败\n点击重试




                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        {renderRichFile()}
      </div>

      <Lightbox
        open={previewOpen}
        close={() => setPreviewOpen(false)}
        slides={richSlides}
        index={previewIndex}
        plugins={[Zoom]}
        controller={{ closeOnBackdropClick: true }}
        styles={{ container: { zIndex: 2000, backgroundColor: 'rgba(0,0,0,0.85)' } }}
      />
    </>
  );
};

export default RichTextMessage;

