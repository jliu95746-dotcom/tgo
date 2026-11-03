import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Camera, Loader2, X } from 'lucide-react';
import ImageCropModal from '@/components/ui/ImageCropModal';
import { staffApi } from '@/services/staffApi';
import type { StaffResponse } from '@/services/api';

interface ProfileEditDialogProps {
  profile: StaffResponse;
  onCancel: () => void;
  onSaved: (profile: StaffResponse) => void;
}

const MAX_AVATAR_BYTES = 5 * 1024 * 1024;

const ProfileEditDialog: React.FC<ProfileEditDialogProps> = ({ profile, onCancel, onSaved }) => {
  const { t } = useTranslation();
  const [nickname, setNickname] = useState(profile.nickname || '');
  const [avatar, setAvatar] = useState<File>();
  const [preview, setPreview] = useState(profile.avatar_url || '');
  const [cropSource, setCropSource] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const nicknameInput = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const savingRef = useRef(false);

  useEffect(() => {
    const previous = document.activeElement;
    nicknameInput.current?.focus();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);

  useEffect(() => () => { if (cropSource) URL.revokeObjectURL(cropSource); }, [cropSource]);

  const close = () => { if (!savingRef.current) onCancel(); };

  const chooseAvatar = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      setError(t('settings.profile.avatarTypeError'));
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setError(t('settings.profile.avatarSizeError'));
      return;
    }
    setError('');
    setCropSource(URL.createObjectURL(file));
  };

  const confirmCrop = (blob: Blob, dataUrl: string) => {
    if (blob.size > MAX_AVATAR_BYTES) {
      setError(t('settings.profile.avatarSizeError'));
    } else {
      setAvatar(new File([blob], 'avatar.png', { type: 'image/png' }));
      setPreview(dataUrl);
      setError('');
    }
    setCropSource('');
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (savingRef.current) return;
    const value = nickname.trim();
    if (!value || Array.from(value).length > 100) {
      setError(t('settings.profile.nicknameError'));
      nicknameInput.current?.focus();
      return;
    }
    savingRef.current = true;
    setSaving(true);
    setError('');
    try {
      const updated = await staffApi.saveMyProfile({ nickname: value }, avatar);
      onSaved(updated);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : t('settings.profile.saveError'));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (cropSource) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab') return;
    const controls = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)');
    if (!controls?.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  return createPortal(
    <>
      <div className="fixed inset-0 z-[9999] bg-black/50 flex items-center justify-center p-4" onClick={close}>
        <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="profile-edit-title"
          className="w-full max-w-md max-h-[90vh] overflow-auto rounded-xl bg-white dark:bg-gray-800 shadow-xl"
          onClick={event => event.stopPropagation()} onKeyDown={handleKeyDown}>
          <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 id="profile-edit-title" className="font-semibold text-gray-900 dark:text-gray-100">{t('settings.profile.edit')}</h2>
            <button type="button" onClick={close} disabled={saving} aria-label={t('common.close', '关闭')}
              className="text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-50"><X className="w-5 h-5" /></button>
          </div>
          <form onSubmit={save} className="p-6 space-y-5">
            <div className="flex flex-col items-center gap-3">
              <div className="w-24 h-24 rounded-xl overflow-hidden bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
                {preview ? <img src={preview} alt={t('settings.profile.avatar')} className="w-full h-full object-cover" />
                  : <span className="text-3xl font-semibold text-gray-500">{(nickname || profile.username).slice(0, 1).toUpperCase()}</span>}
              </div>
              <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" disabled={saving} onChange={chooseAvatar} />
              <button type="button" onClick={() => fileInput.current?.click()} disabled={saving}
                className="flex items-center gap-2 text-sm text-blue-600 dark:text-blue-400 disabled:opacity-50"><Camera className="w-4 h-4" />{t('settings.profile.changeAvatar')}</button>
              <p className="text-xs text-gray-500 dark:text-gray-400">{t('settings.profile.avatarHint')}</p>
            </div>
            <div>
              <label htmlFor="profile-nickname" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">{t('settings.profile.nickname')}</label>
              <input id="profile-nickname" ref={nicknameInput} value={nickname} disabled={saving} onChange={event => setNickname(event.target.value)}
                aria-describedby="profile-nickname-hint" autoComplete="nickname"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:outline-none" />
              <p id="profile-nickname-hint" className="mt-2 text-xs text-gray-500 dark:text-gray-400">{t('settings.profile.nicknameHint')}</p>
            </div>
            {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
            <div className="flex justify-end gap-3">
              <button type="button" onClick={close} disabled={saving} className="px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 disabled:opacity-50">{t('common.cancel', '取消')}</button>
              <button type="submit" disabled={saving || !!cropSource} className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white flex items-center gap-2 disabled:opacity-50">
                {saving && <Loader2 className="w-4 h-4 animate-spin" />}{t(saving ? 'settings.profile.saving' : 'common.save', saving ? '保存中…' : '保存')}
              </button>
            </div>
          </form>
        </div>
      </div>
      <ImageCropModal isOpen={!!cropSource} imageSrc={cropSource} title={t('settings.profile.cropAvatar')}
        onCancel={() => setCropSource('')} onConfirm={confirmCrop} />
    </>, document.body,
  );
};

export default ProfileEditDialog;
