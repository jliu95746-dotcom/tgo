import { MessagePayloadType, type MessagePayload, type PayloadRichTextImage } from '@/types';
import type { DeliveryJson, StaffDeliveryPayload } from '@/types/staffDelivery';

function objectValue(value: DeliveryJson | undefined): { [key: string]: DeliveryJson } | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : undefined;
}

function imageValue(value: DeliveryJson): PayloadRichTextImage | undefined {
  const image = objectValue(value);
  if (!image || typeof image.url !== 'string') return undefined;
  return {
    url: image.url,
    width: typeof image.width === 'number' ? image.width : undefined,
    height: typeof image.height === 'number' ? image.height : undefined,
  };
}

/** Validate recovered server JSON before handing it to a typed message renderer. */
export function restoreDeliveryPayload(payload: StaffDeliveryPayload): MessagePayload | undefined {
  const content = typeof payload.content === 'string' ? payload.content : '';
  switch (payload.type) {
    case MessagePayloadType.TEXT:
      return { type: MessagePayloadType.TEXT, content };
    case MessagePayloadType.IMAGE: {
      const image = imageValue(payload);
      return image ? { type: MessagePayloadType.IMAGE, content, ...image } : undefined;
    }
    case MessagePayloadType.FILE:
      if (typeof payload.url !== 'string' || typeof payload.name !== 'string') return undefined;
      return {
        type: MessagePayloadType.FILE, url: payload.url, name: payload.name,
        size: typeof payload.size === 'number' ? payload.size : undefined,
      };
    case MessagePayloadType.RICH_TEXT: {
      const file = objectValue(payload.file);
      const images = Array.isArray(payload.images) ? payload.images.map(imageValue).filter(
        (image): image is PayloadRichTextImage => image !== undefined,
      ) : [];
      return {
        type: MessagePayloadType.RICH_TEXT, content, images,
        file: file && typeof file.url === 'string' && typeof file.name === 'string'
          ? { url: file.url, name: file.name, size: typeof file.size === 'number' ? file.size : undefined }
          : undefined,
      };
    }
    default:
      return undefined;
  }
}
