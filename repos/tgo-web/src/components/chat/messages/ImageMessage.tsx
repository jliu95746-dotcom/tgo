import React from 'react';
import { useTranslation } from 'react-i18next';
import { MessagePayloadType, type Message } from '@/types';
import Lightbox from 'yet-another-react-lightbox';
import Zoom from 'yet-another-react-lightbox/plugins/zoom';
import 'yet-another-react-lightbox/styles.css';
import { useChatFileUrls } from '@/hooks/useChatFileUrls';

/**
 * ImageMessage renders single image messages with preview/lightbox.
 */
export interface MessageComponentProps {
  message: Message;
  isStaff: boolean;
}

const ImageMessage: React.FC<MessageComponentProps> = ({ message, isStaff }) => {
  const { t } = useTranslation();
  const typedPayload = message.payload as any | undefined;

  const sourceUrl = ((typedPayload?.type === MessagePayloadType.IMAGE && typedPayload?.url) || message.metadata?.image_url || message.metadata?.image_preview_url || '') as string;
  const [imageUrl] = useChatFileUrls([sourceUrl]);
  let imgW = (typedPayload?.type === MessagePayloadType.IMAGE && typeof typedPayload?.width === 'number') ? typedPayload.width : (Number(message.metadata?.image_width) || 0);
  let imgH = (typedPayload?.type === MessagePayloadType.IMAGE && typeof typedPayload?.height === 'number') ? typedPayload.height : (Number(message.metadata?.image_height) || 0);
  if (!imgW || !imgH) { imgW = 200; imgH = 200; }
  const MAX_W = 240; const MAX_H = 320;
  let dispW = imgW; let dispH = imgH;
  if (dispW > MAX_W) { const r = MAX_W / dispW; dispW = MAX_W; dispH = Math.round(dispH * r); }
  if (dispH > MAX_H) { const r = MAX_H / dispH; dispH = MAX_H; dispW = Math.round(dispW * r); }

  const uploading = message.metadata?.upload_status === 'uploading';
  const uploadProgress = typeof message.metadata?.upload_progress === 'number' ? (message.metadata!.upload_progress as number) : undefined;
  const uploadError = message.metadata?.upload_status === 'error';

  const [previewOpen, setPreviewOpen] = React.useState(false);

  const bubbleClass = isStaff
    ? 'relative rounded-lg overflow-hidden border border-blue-200 dark:border-blue-700 bg-white dark:bg-gray-700 '
    : 'relative rounded-lg overflow-hidden border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800 ';

  return (
    <>
      <div
        className={bubbleClass + (imageUrl && !uploading ? 'cursor-zoom-in hover:opacity-95' : 'cursor-default')}
        style={{ width: dispW, height: dispH }}
        role="button"
        aria-label={t('chat.messages.image.previewAria', '预览图片')}
        aria-disabled={uploading || !imageUrl}
        onClick={() => { if (imageUrl && !uploading) setPreviewOpen(true); }}
      >
        {imageUrl ? (
          <img src={imageUrl} alt={t('chat.messages.image.alt', '图片')} className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full bg-gray-100 dark:bg-gray-700" />
        )}
        {uploading && (
          <div className="absolute inset-0 bg-black/40 flex items-center justify-center text-white text-xs">
            {typeof uploadProgress === 'number' ? `${uploadProgress}%` : t('chat.messages.image.uploading', '上传中')}
          </div>
        )}
        {uploadError && (
          <div className="absolute inset-0 bg-red-500/20 dark:bg-red-900/40 flex items-center justify-center text-red-700 dark:text-red-400 text-xs">{t('chat.messages.image.uploadFailed', '上传失败')}</div>
        )}
      </div>

      <Lightbox
        open={previewOpen}
        close={() => setPreviewOpen(false)}
        slides={[{ src: imageUrl }]}
        index={0}
        plugins={[Zoom]}
        controller={{ closeOnBackdropClick: true }}
        styles={{ container: { zIndex: 2000, backgroundColor: 'rgba(0,0,0,0.85)' } }}
      />
    </>
  );
};

export default ImageMessage;

