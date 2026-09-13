import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Cropper, { Area } from 'react-easy-crop';

interface ImageCropModalProps {
  isOpen: boolean;
  imageSrc: string; // object URL or data URL
  aspect?: number; // default 1
  mimeType?: string; // default 'image/png'
  title?: string; // modal title, default '裁剪图片'
  onCancel: () => void;
  onConfirm: (blob: Blob, dataUrl: string) => void;
}

// Helper: crop image via canvas and return Blob + dataURL
async function cropImageToBlob(
  imageSrc: string,
  cropPixels: Area,
  mimeType: string = 'image/png',
  quality = 0.92
): Promise<{ blob: Blob; dataUrl: string }> {
  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = imageSrc;
  });

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('无法创建画布上下文');

  const { width, height, x, y } = cropPixels;
  canvas.width = Math.max(1, Math.floor(width));
  canvas.height = Math.max(1, Math.floor(height));

  ctx.drawImage(
    img,
    x,
    y,
    width,
    height,
    0,
    0,
    canvas.width,
    canvas.height
  );

  const blob: Blob = await new Promise((resolve) => {
    canvas.toBlob((b) => resolve(b as Blob), mimeType, quality);
  });

  const dataUrl = canvas.toDataURL(mimeType, quality);
  return { blob, dataUrl };
}

const ImageCropModal: React.FC<ImageCropModalProps> = ({
  isOpen,
  imageSrc,
  aspect = 1,
  mimeType = 'image/png',
  title = '裁剪图片',
  onCancel,
  onConfirm,
}) => {
  const [crop, setCrop] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [croppedAreaPixels, setCroppedAreaPixels] = useState<Area | null>(null);
  const [previewDataUrl, setPreviewDataUrl] = useState<string>('');
  const workingRef = useRef(false);

  useEffect(() => {
    if (!isOpen) {
      setZoom(1);
      setCrop({ x: 0, y: 0 });
      setCroppedAreaPixels(null);
      setPreviewDataUrl('');
    }
  }, [isOpen]);

  const onCropComplete = useCallback(async (_: Area, cropped: Area) => {
    setCroppedAreaPixels(cropped);
    // Update preview (best-effort)
    try {
      if (!imageSrc) return;
      // Avoid overlapping work
      if (workingRef.current) return;
      workingRef.current = true;
      const { dataUrl } = await cropImageToBlob(imageSrc, cropped, mimeType);
      setPreviewDataUrl(dataUrl);
    } catch {
      // Silent preview failure; final confirm will show proper errors if any
    } finally {
      workingRef.current = false;
    }
  }, [imageSrc, mimeType]);

  const handleConfirm = useCallback(async () => {
    try {
      if (!croppedAreaPixels) throw new Error('请先调整裁剪区域');
      const { blob, dataUrl } = await cropImageToBlob(imageSrc, croppedAreaPixels, mimeType);
      onConfirm(blob, dataUrl);
    } catch (err) {
      alert((err as Error)?.message || '裁剪失败，请重试');
    }
  }, [croppedAreaPixels, imageSrc, mimeType, onConfirm]);

  if (!isOpen) return null;
  if (typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-[10000] bg-black/50 flex items-center justify-center p-4" onClick={onCancel}>
      <div
        className="bg-white dark:bg-gray-800 w-full max-w-2xl rounded-lg shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-800 dark:text-gray-100">{title}</h3>
          <button onClick={onCancel} className="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-sm">取消</button>
        </div>

        <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-4" style={{ minHeight: 360 }}>
          <div className="md:col-span-2 relative rounded-md overflow-hidden bg-black/5 dark:bg-black/20" style={{ height: 300 }}>
            <Cropper
              image={imageSrc}
              crop={crop}
              zoom={zoom}
              aspect={aspect}
              onCropChange={setCrop}
              onZoomChange={setZoom}
              onCropComplete={onCropComplete}
              cropShape="rect"
              showGrid={false}
              restrictPosition
            />
          </div>
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">缩放</label>
              <input
                type="range"
                min={1}
                max={3}
                step={0.01}
                value={zoom}
                onChange={(e) => setZoom(parseFloat(e.target.value))}
                className="w-full"
              />
            </div>
            <div>
              <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">预览（1:1）</div>
              <div className="w-24 h-24 border dark:border-gray-600 rounded-md overflow-hidden bg-gray-50 dark:bg-gray-700 flex items-center justify-center">
                {previewDataUrl ? (
                  <img src={previewDataUrl} alt="预览" className="w-full h-full object-cover" />
                ) : (
                  <span className="text-gray-400 dark:text-gray-500 text-xs">暂无</span>
                )}
              </div>
              <div className="text-[10px] text-gray-400 dark:text-gray-500 mt-2">建议尺寸：72x72 像素</div>
            </div>
          </div>
        </div>

        <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex justify-end gap-2">
          <button onClick={onCancel} className="px-3 py-1.5 text-sm rounded-md bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200">取消</button>
          <button onClick={handleConfirm} className="px-3 py-1.5 text-sm rounded-md bg-blue-600 dark:bg-blue-700 hover:bg-blue-700 dark:hover:bg-blue-800 text-white">确认</button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default ImageCropModal;

