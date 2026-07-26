import React, { useEffect, useState } from 'react';
import { Loader2, Sparkles, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useToast } from '@/hooks/useToast';
import SkillsApiService, { type SkillDetail } from '@/services/skillsApi';

interface HumanizationSkillModalProps {
  isOpen: boolean;
  onClose: () => void;
  skill?: SkillDetail | null;
  onSaved: () => void;
}

const HumanizationSkillModal: React.FC<HumanizationSkillModalProps> = ({
  isOpen,
  onClose,
  skill,
  onSaved,
}) => {
  const { t } = useTranslation();
  const { showSuccess, showError } = useToast();
  const [displayName, setDisplayName] = useState('');
  const [internalName, setInternalName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setDisplayName(skill?.display_name || '');
    setInternalName(skill?.name || '');
    setDescription(skill?.description || '');
    setInstructions(skill?.instructions || '');
  }, [isOpen, skill]);

  if (!isOpen) return null;

  const handleSubmit = async () => {
    if (!displayName.trim() || !description.trim()) {
      showError(
        t('skills.humanization.validationTitle', '请完善拟人技能信息'),
        t('skills.humanization.validationMessage', '名称和描述不能为空'),
      );
      return;
    }
    setIsSaving(true);
    try {
      if (skill) {
        await SkillsApiService.updateSkill(skill.name, {
          description: description.trim(),
          instructions: instructions.trim() || undefined,
          metadata: { display_name: displayName.trim() },
        });
      } else {
        await SkillsApiService.createHumanizationSkill({
          name: internalName.trim() || undefined,
          display_name: displayName.trim(),
          description: description.trim(),
        });
      }
      showSuccess(
        skill
          ? t('skills.humanization.editSuccess', '拟人技能已保存')
          : t('skills.humanization.createSuccess', '拟人技能已创建'),
      );
      onSaved();
      onClose();
    } catch (error) {
      showError(
        t('skills.humanization.saveFailed', '保存拟人技能失败'),
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <span className="rounded-xl bg-violet-100 p-2 text-violet-600 dark:bg-violet-900/30 dark:text-violet-300">
              <Sparkles className="h-5 w-5" />
            </span>
            <div>
              <h2 className="font-bold text-gray-900 dark:text-gray-100">
                {skill
                  ? t('skills.humanization.editTitle', '编辑拟人技能')
                  : t('skills.humanization.createTitle', '创建拟人技能')}
              </h2>
              <p className="text-xs text-gray-500">
                {t('skills.humanization.modalHint', '人工修改先进入待更新区，手动更新后才会生效')}
              </p>
            </div>
          </div>
          <button className="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800" onClick={onClose}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[70vh] space-y-5 overflow-y-auto p-6">
          <label className="block space-y-2">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('skills.humanization.displayName', '显示名称')}
            </span>
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder={t('skills.humanization.displayNamePlaceholder', '例如：售后真人话术')}
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-gray-700 dark:bg-gray-800"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('skills.humanization.internalName', '内部名称')}
            </span>
            <input
              value={internalName}
              onChange={(event) => setInternalName(event.target.value.toLowerCase())}
              disabled={Boolean(skill)}
              placeholder="humanization-after-sales"
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 font-mono text-sm outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 disabled:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:disabled:bg-gray-800/50"
            />
            <span className="text-xs text-gray-400">
              {t('skills.humanization.internalNameHint', '可留空自动生成；仅支持小写字母、数字和连字符')}
            </span>
          </label>

          <label className="block space-y-2">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('skills.form.description', '描述')}
            </span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              placeholder={t('skills.humanization.descriptionPlaceholder', '说明要训练的语气和适用场景')}
              className="w-full resize-none rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-gray-700 dark:bg-gray-800"
            />
          </label>

          {skill && (
            <label className="block space-y-2">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                {t('skills.form.instructions', '技能指令 (Markdown)')}
              </span>
              <textarea
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                rows={10}
                className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 font-mono text-xs outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-gray-700 dark:bg-gray-800"
              />
            </label>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4 dark:border-gray-800">
          <button onClick={onClose} className="rounded-xl px-4 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800">
            {t('common.cancel', '取消')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSaving}
            className="flex items-center gap-2 rounded-xl bg-violet-600 px-5 py-2 text-sm font-bold text-white hover:bg-violet-700 disabled:opacity-60"
          >
            {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
            {skill ? t('common.save', '保存') : t('skills.humanization.createAction', '创建拟人技能')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default HumanizationSkillModal;
