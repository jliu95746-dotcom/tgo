/**
 * SkillFormModal
 *
 * - **Create mode** (no `skill` prop): Import a skill from a local SKILL.md or ZIP file.
 * - **Edit mode** (`skill` prop provided): Edit description / instructions / tags of
 *   an existing project-private skill.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, Zap, Loader2, Tag, Plus, Upload } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '@/hooks/useToast';
import SkillsApiService, {
  type SkillUpdateRequest,
  type SkillDetail,
} from '@/services/skillsApi';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface SkillFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Passing a SkillDetail switches the modal to "edit" mode. */
  skill?: SkillDetail | null;
  /** Called after a successful import/update so the parent can refresh its list. */
  onSaved?: () => void;
}

// ---------------------------------------------------------------------------
// Edit form state
// ---------------------------------------------------------------------------

interface EditFormData {
  display_name: string;
  description: string;
  instructions: string;
  author: string;
  license: string;
  tags: string[];
  is_featured: boolean;
}

const EMPTY_EDIT_FORM: EditFormData = {
  display_name: '',
  description: '',
  instructions: '',
  author: '',
  license: '',
  tags: [],
  is_featured: false,
};

interface FormErrors {
  file?: string;
  display_name?: string;
  description?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const SkillFormModal: React.FC<SkillFormModalProps> = ({
  isOpen,
  onClose,
  skill,
  onSaved,
}) => {
  const { t } = useTranslation();
  const { showSuccess, showError } = useToast();

  const isEditMode = !!skill;

  // Import mode state
  const [file, setFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState('');
  const savingRef = useRef(false);

  // Edit mode state
  const [editForm, setEditForm] = useState<EditFormData>(EMPTY_EDIT_FORM);
  const [tagInput, setTagInput] = useState('');

  // Common state
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSaving, setIsSaving] = useState(false);

  // ---------------------------------------------------------------------------
  // Initialise / reset when the modal opens
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!isOpen) return;
    setErrors({});
    setIsSaving(false);

    if (skill) {
      // Edit mode: populate form
      setEditForm({
        display_name: skill.display_name || skill.name,
        description: skill.description,
        instructions: skill.instructions ?? '',
        author: skill.author ?? '',
        license: skill.license ?? '',
        tags: skill.tags ?? [],
        is_featured: skill.is_featured,
      });
      setTagInput('');
    } else {
      // Import mode: clear
      setFile(null);
      setDisplayName('');
    }
  }, [isOpen, skill]);

  // ---------------------------------------------------------------------------
  // Validation
  // ---------------------------------------------------------------------------
  const validateImport = useCallback((): boolean => {
    const e: FormErrors = {};
    if (!file) e.file = '请选择 SKILL.md 或 ZIP 技能包';
    else if (!(file.name.toLowerCase() === 'skill.md' || file.name.toLowerCase().endsWith('.zip'))) e.file = '仅支持 SKILL.md 或 ZIP 文件';
    else if (file.size > 10 * 1024 * 1024) e.file = '文件不能超过 10 MB';
    setErrors(e);
    return Object.keys(e).length === 0;
  }, [file]);

  const validateEdit = useCallback((): boolean => {
    const e: FormErrors = {};

    if (!editForm.display_name.trim()) e.display_name = '请输入技能名称';
    if (!editForm.description.trim()) {
      e.description = t('skills.form.errors.descRequired', '请输入技能描述');
    }

    setErrors(e);
    return Object.keys(e).length === 0;
  }, [editForm, t]);

  // ---------------------------------------------------------------------------
  // Tag helpers (edit mode only)
  // ---------------------------------------------------------------------------
  const addTag = () => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !editForm.tags.includes(tag)) {
      setEditForm((prev) => ({ ...prev, tags: [...prev.tags, tag] }));
    }
    setTagInput('');
  };

  const removeTag = (tag: string) => {
    setEditForm((prev) => ({
      ...prev,
      tags: prev.tags.filter((t) => t !== tag),
    }));
  };

  const handleTagKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag();
    }
  };

  // ---------------------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------------------
  const handleSubmit = async () => {
    if (savingRef.current) return;
    if (isEditMode) {
      if (!validateEdit()) return;
    } else {
      if (!validateImport()) return;
    }

    savingRef.current = true;
    setIsSaving(true);
    try {
      if (isEditMode) {
        const payload: SkillUpdateRequest = {
          display_name: editForm.display_name.trim(),
          description: editForm.description,
          instructions: editForm.instructions,
          author: editForm.author || undefined,
          license: editForm.license || undefined,
          tags: editForm.tags,
          is_featured: editForm.is_featured,
        };
        await SkillsApiService.updateSkill(skill!.name, payload);
        showSuccess(t('skills.form.updateSuccess', '技能已更新'));
      } else {
        await SkillsApiService.importSkill({ file: file!, display_name: displayName.trim() || undefined });
        showSuccess('技能已导入，检查内容后可开启使用');
      }
      onSaved?.();
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showError(
        isEditMode
          ? t('skills.form.updateFailed', '更新技能失败')
          : t('skills.import.failed', '导入技能失败'),
        msg,
      );
    } finally {
      savingRef.current = false;
      setIsSaving(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={() => { if (!savingRef.current) onClose(); }}
      />

      {/* Modal */}
      <div className="relative w-full max-w-2xl max-h-[90vh] bg-white dark:bg-gray-900 rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-800">
          <div className="flex items-center gap-2">
            {isEditMode ? (
              <Zap className="w-5 h-5 text-blue-600" />
            ) : (
              <Upload className="w-5 h-5 text-gray-800 dark:text-gray-200" />
            )}
            <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
              {isEditMode
                ? t('skills.form.editTitle', '编辑技能')
                : '本地导入技能'}
            </h2>
          </div>
          <button
            onClick={() => { if (!savingRef.current) onClose(); }}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {isEditMode ? (
            /* ============================================================
             * EDIT MODE
             * ============================================================ */
            <>
              {/* Name (read-only) */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('skills.form.name', '技能名称')}
                </label>
                <input
                  type="text"
                  value={editForm.display_name}
                  maxLength={100}
                  aria-label="技能名称"
                  onChange={(event) => setEditForm((previous) => ({ ...previous, display_name: event.target.value }))}
                  className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm outline-none focus:border-blue-500"
                />
              </div>

              {errors.display_name && <p className="text-xs text-red-500">{errors.display_name}</p>}
              <p className="text-xs text-gray-500">支持中文；内部标识 {skill!.name} 保持不变。</p>

              {/* Description */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('skills.form.description', '描述')}{' '}
                  <span className="text-red-500">*</span>
                </label>
                <textarea
                  rows={2}
                  value={editForm.description}
                  onChange={(e) =>
                    setEditForm((prev) => ({
                      ...prev,
                      description: e.target.value,
                    }))
                  }
                  className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors resize-none"
                />
                {errors.description && (
                  <p className="mt-1 text-xs text-red-500">
                    {errors.description}
                  </p>
                )}
              </div>

              {/* Instructions */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('skills.form.instructions', '技能指令 (Markdown)')}
                </label>
                <textarea
                  rows={8}
                  value={editForm.instructions}
                  onChange={(e) =>
                    setEditForm((prev) => ({
                      ...prev,
                      instructions: e.target.value,
                    }))
                  }
                  className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm font-mono outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors resize-y"
                />
              </div>

              {/* Author & License */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    {t('skills.form.author', '作者')}
                  </label>
                  <input
                    type="text"
                    value={editForm.author}
                    onChange={(e) =>
                      setEditForm((prev) => ({
                        ...prev,
                        author: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    {t('skills.form.license', '许可证')}
                  </label>
                  <input
                    type="text"
                    value={editForm.license}
                    onChange={(e) =>
                      setEditForm((prev) => ({
                        ...prev,
                        license: e.target.value,
                      }))
                    }
                    placeholder="MIT"
                    className="w-full px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
                  />
                </div>
              </div>

              {/* Tags */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  {t('skills.form.tags', '标签')}
                </label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {editForm.tags.map((tag) => (
                    <span
                      key={tag}
                      className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-lg text-xs font-medium"
                    >
                      <Tag className="w-3 h-3" />
                      {tag}
                      <button
                        type="button"
                        onClick={() => removeTag(tag)}
                        className="ml-0.5 hover:text-red-500 transition-colors"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={handleTagKeyDown}
                    placeholder={t(
                      'skills.form.tagPlaceholder',
                      '输入标签后按回车',
                    )}
                    className="flex-1 px-3 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
                  />
                  <button
                    type="button"
                    onClick={addTag}
                    disabled={!tagInput.trim()}
                    className="px-3 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-xl transition-colors disabled:opacity-40"
                  >
                    <Plus className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </>
          ) : (
            /* ============================================================
             * IMPORT MODE
             * ============================================================ */
            <>
              <div className="space-y-2">
                <label htmlFor="local-skill-file" className="block text-sm font-medium">技能文件</label>
                <input id="local-skill-file" type="file" accept=".md,.zip" disabled={isSaving}
                  onChange={(event) => setFile(event.target.files?.[0] || null)}
                  className="block w-full rounded-xl border border-gray-300 p-3 text-sm dark:border-gray-700" />
                {errors.file && <p className="text-xs text-red-500">{errors.file}</p>}
                <p className="text-xs text-gray-500">支持单个 SKILL.md 或一个技能目录的 ZIP 包，最大 10 MB。</p>
              </div>
              <div className="space-y-2">
                <label htmlFor="local-skill-name" className="block text-sm font-medium">技能名称（可选，支持中文）</label>
                <input id="local-skill-name" value={displayName} maxLength={100} disabled={isSaving}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="留空则使用文件中的名称"
                  className="w-full rounded-xl border border-gray-300 bg-transparent px-3 py-2 text-sm dark:border-gray-700" />
              </div>
              <p className="rounded-xl bg-blue-50 p-4 text-sm text-blue-800 dark:bg-blue-900/20 dark:text-blue-200">
                SKILL.md 需包含 name、description 和指令正文。ZIP 可带 references、scripts、assets 文件夹。
                仅导入可信来源；技能默认关闭，启用后 AI 可能执行包内脚本。
              </p>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-800">
          <button
            onClick={() => { if (!savingRef.current) onClose(); }}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-colors"
          >
            {t('common.cancel', '取消')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSaving}
            className="flex items-center gap-2 px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-bold rounded-xl shadow-lg shadow-blue-200 dark:shadow-none transition-all active:scale-95"
          >
            {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
            {isEditMode
              ? t('common.save', '保存')
              : t('skills.import.submit', '导入技能')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SkillFormModal;
