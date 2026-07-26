import React, { useEffect, useRef, useState } from 'react';
import {
  Clock,
  Eye,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  User,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

export interface Skill {
  name: string;
  description: string;
  author: string | null;
  is_official: boolean;
  is_featured: boolean;
  tags: string[];
  updated_at: string | null;
  enabled: boolean;
  skill_type: 'standard' | 'humanization';
  display_name: string | null;
  pending_training_count: number;
  published_version: number;
}

interface SkillCardProps {
  skill: Skill;
  enabled: boolean;
  onToggle: (skill: Skill, enabled: boolean) => void;
  onClick: (skill: Skill) => void;
  onDelete: (skill: Skill) => void;
  onEdit?: (skill: Skill) => void;
  onApplyTraining?: (skill: Skill) => void;
  isApplying?: boolean;
}

function formatDate(iso: string | null): string {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

const SkillCard: React.FC<SkillCardProps> = ({
  skill,
  enabled,
  onToggle,
  onClick,
  onDelete,
  onEdit,
  onApplyTraining,
  isApplying = false,
}) => {
  const { t } = useTranslation();
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const isHumanization = skill.skill_type === 'humanization';
  const title = skill.display_name || skill.name;

  useEffect(() => {
    const closeMenu = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false);
      }
    };
    if (showMenu) document.addEventListener('mousedown', closeMenu);
    return () => document.removeEventListener('mousedown', closeMenu);
  }, [showMenu]);

  return (
    <div
      className={`group relative flex h-full cursor-pointer flex-col justify-between rounded-2xl border bg-white p-5 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-xl dark:bg-gray-800 ${
        isHumanization
          ? 'border-violet-200 dark:border-violet-800/60'
          : 'border-gray-100 dark:border-gray-700'
      } ${!enabled && !isHumanization ? 'opacity-60' : ''}`}
      onClick={() => onClick(skill)}
    >
      <div>
        <div className="mb-3 flex items-center justify-between">
          <div className="flex min-w-0 flex-1 items-center gap-2 pr-2">
            <h3 className="truncate text-base font-bold text-gray-900 dark:text-gray-100" title={title}>
              {title}
            </h3>
            {(skill.is_featured || isHumanization) && (
              <Sparkles className={`h-4 w-4 shrink-0 ${isHumanization ? 'text-violet-500' : 'text-blue-500'}`} />
            )}
          </div>

          <div className="shrink-0" onClick={(event) => event.stopPropagation()}>
            {isHumanization ? (
              <span className="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-semibold text-violet-700 dark:bg-violet-900/30 dark:text-violet-300">
                v{skill.published_version}
              </span>
            ) : (
              <button
                onClick={() => onToggle(skill, !enabled)}
                className="relative inline-flex h-5 w-9 items-center rounded-full transition-colors"
                style={{ backgroundColor: enabled ? '#3b82f6' : '#d1d5db' }}
                title={enabled ? t('skills.disable', '禁用技能') : t('skills.enable', '启用技能')}
              >
                <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
              </button>
            )}
          </div>
        </div>

        <div className="mb-3 flex flex-wrap gap-1">
          {isHumanization && (
            <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-600 dark:bg-violet-900/30 dark:text-violet-300">
              {t('skills.humanization.badge', '拟人技能')}
            </span>
          )}
          {skill.tags.slice(0, isHumanization ? 2 : 3).map((tag) => (
            <span key={tag} className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
              {tag}
            </span>
          ))}
        </div>

        <p className="mb-5 h-[4.5em] line-clamp-3 text-sm leading-relaxed text-gray-600 dark:text-gray-400">
          {skill.description}
        </p>

        {isHumanization && skill.pending_training_count > 0 && (
          <button
            onClick={(event) => {
              event.stopPropagation();
              onApplyTraining?.(skill);
            }}
            disabled={isApplying}
            className="mb-4 flex w-full items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-semibold text-violet-700 hover:bg-violet-100 disabled:opacity-60 dark:border-violet-800 dark:bg-violet-900/20 dark:text-violet-300"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isApplying ? 'animate-spin' : ''}`} />
            {t('skills.humanization.applyPending', '更新技能（{{count}}条）', { count: skill.pending_training_count })}
          </button>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-gray-100 pt-4 dark:border-gray-700/50">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
            {skill.is_official ? <ShieldCheck className="h-3.5 w-3.5 text-blue-500" /> : <User className="h-3.5 w-3.5" />}
            <span className="max-w-[80px] truncate">
              {skill.is_official ? t('common.official', '官方') : skill.author || '-'}
            </span>
          </div>
          <div className="h-3 w-px bg-gray-200 dark:bg-gray-700" />
          <div className="flex items-center gap-1.5 text-xs text-gray-400">
            <Clock className="h-3 w-3" />
            <span>{formatDate(skill.updated_at)}</span>
          </div>
        </div>

        {!skill.is_official && (
          <div className="relative" ref={menuRef} onClick={(event) => event.stopPropagation()}>
            <button onClick={() => setShowMenu((value) => !value)} className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700">
              <MoreHorizontal className="h-4 w-4" />
            </button>
            {showMenu && (
              <div className="absolute bottom-full right-0 z-20 mb-2 w-32 rounded-xl border border-gray-100 bg-white py-1 shadow-xl dark:border-gray-700 dark:bg-gray-800">
                <button onClick={() => { setShowMenu(false); onClick(skill); }} className="flex w-full items-center px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <Eye className="mr-2 h-3.5 w-3.5" /> {t('common.view', '查看')}
                </button>
                {isHumanization && onEdit && (
                  <button onClick={() => { setShowMenu(false); onEdit(skill); }} className="flex w-full items-center px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <Pencil className="mr-2 h-3.5 w-3.5" /> {t('common.edit', '编辑')}
                  </button>
                )}
                <div className="my-1 h-px bg-gray-100 dark:bg-gray-700" />
                <button onClick={() => { setShowMenu(false); onDelete(skill); }} className="flex w-full items-center px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20">
                  <Trash2 className="mr-2 h-3.5 w-3.5" /> {t('common.delete', '删除')}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default SkillCard;
