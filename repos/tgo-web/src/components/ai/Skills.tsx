import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Github, Sparkles } from 'lucide-react';
import { LuLoader, LuSearch, LuZap } from 'react-icons/lu';
import { useTranslation } from 'react-i18next';

import { useToast } from '@/hooks/useToast';
import SkillsApiService, { type SkillDetail, type SkillSummary } from '@/services/skillsApi';

import HumanizationSkillModal from './HumanizationSkillModal';
import SkillCard, { type Skill } from './SkillCard';
import SkillDetailModal from './SkillDetailModal';
import SkillFormModal from './SkillFormModal';

const Skills: React.FC = () => {
  const { t } = useTranslation();
  const { showSuccess, showError } = useToast();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [isHumanizationOpen, setIsHumanizationOpen] = useState(false);
  const [editingHumanization, setEditingHumanization] = useState<SkillDetail | null>(null);
  const [viewingSkillName, setViewingSkillName] = useState<string | null>(null);
  const [applyingSkillName, setApplyingSkillName] = useState<string | null>(null);

  const fetchSkills = useCallback(async () => {
    try {
      setIsLoading(true);
      const list: SkillSummary[] = await SkillsApiService.listSkills();
      setSkills(list.map((skill) => ({ ...skill })));
    } catch (error) {
      showError(
        t('skills.fetchFailed', '获取技能列表失败'),
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setIsLoading(false);
    }
  }, [showError, t]);

  useEffect(() => {
    void fetchSkills();
  }, [fetchSkills]);

  const filteredSkills = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return skills;
    return skills.filter((skill) =>
      [skill.name, skill.display_name || '', skill.description]
        .some((value) => value.toLowerCase().includes(query)),
    );
  }, [searchQuery, skills]);

  const humanizationSkills = filteredSkills.filter((skill) => skill.skill_type === 'humanization');
  const standardSkills = filteredSkills.filter((skill) => skill.skill_type !== 'humanization');

  const handleToggle = useCallback(async (skill: Skill, enabled: boolean) => {
    setSkills((current) => current.map((item) => item.name === skill.name ? { ...item, enabled } : item));
    try {
      await SkillsApiService.toggleSkill(skill.name, enabled);
    } catch (error) {
      setSkills((current) => current.map((item) => item.name === skill.name ? { ...item, enabled: !enabled } : item));
      showError(
        t('skills.toggleFailed', '切换技能状态失败'),
        error instanceof Error ? error.message : String(error),
      );
    }
  }, [showError, t]);

  const handleDelete = async (skill: Skill) => {
    if (skill.is_official) return;
    const label = skill.display_name || skill.name;
    if (!window.confirm(t('skills.deleteConfirm', '确定要删除技能 "{{name}}" 吗？此操作不可撤销。', { name: label }))) return;
    try {
      await SkillsApiService.deleteSkill(skill.name);
      showSuccess(t('skills.deleteSuccess', '技能已删除'));
      await fetchSkills();
    } catch (error) {
      showError(
        t('skills.deleteFailed', '删除技能失败'),
        error instanceof Error ? error.message : String(error),
      );
    }
  };

  const handleEditHumanization = async (skill: Skill) => {
    try {
      const detail = await SkillsApiService.getSkill(skill.name);
      setEditingHumanization(detail);
      setIsHumanizationOpen(true);
    } catch (error) {
      showError(
        t('skills.fetchDetailFailed', '获取技能详情失败'),
        error instanceof Error ? error.message : String(error),
      );
    }
  };

  const handleApplyTraining = async (skill: Skill) => {
    try {
      setApplyingSkillName(skill.name);
      const result = await SkillsApiService.applyHumanizationTraining(skill.name);
      showSuccess(
        t('skills.humanization.applySuccess', '已更新 {{count}} 条训练内容，v{{version}} 开始生效', {
          count: result.applied_count,
          version: result.published_version,
        }),
      );
      await fetchSkills();
    } catch (error) {
      showError(
        t('skills.humanization.applyFailed', '更新拟人技能失败'),
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setApplyingSkillName(null);
    }
  };

  const renderGrid = (items: Skill[]) => (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {items.map((skill) => (
        <SkillCard
          key={skill.name}
          skill={skill}
          enabled={skill.enabled}
          onToggle={handleToggle}
          onClick={(item) => setViewingSkillName(item.name)}
          onDelete={handleDelete}
          onEdit={handleEditHumanization}
          onApplyTraining={handleApplyTraining}
          isApplying={applyingSkillName === skill.name}
        />
      ))}
    </div>
  );

  return (
    <main className="flex h-full flex-grow flex-col overflow-hidden bg-[#f8fafc] dark:bg-gray-950">
      <header className="sticky top-0 z-30 flex flex-col justify-between gap-4 border-b border-gray-200/50 bg-white/80 px-8 py-5 backdrop-blur-xl md:flex-row md:items-center dark:border-gray-800/50 dark:bg-gray-900/80">
        <div>
          <h2 className="flex items-center gap-2 text-2xl font-bold text-gray-900 dark:text-gray-100">
            <LuZap className="h-7 w-7 text-blue-600" />
            {t('skills.title', '技能管理')}
          </h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {t('skills.subtitle', '为 AI 员工定义可复用的专业指令集')}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="group relative hidden sm:block">
            <LuSearch className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400 group-focus-within:text-blue-500" />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder={t('skills.search.placeholder', '搜索技能...')}
              className="w-48 rounded-xl bg-gray-100/60 py-2 pl-10 pr-4 text-sm outline-none focus:ring-2 focus:ring-blue-500/20 lg:w-64 dark:bg-gray-800/60"
            />
          </div>
          <button
            onClick={() => { setEditingHumanization(null); setIsHumanizationOpen(true); }}
            className="flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-bold text-white shadow-lg shadow-violet-200 transition hover:bg-violet-700 dark:shadow-none"
          >
            <Sparkles className="h-4 w-4" />
            {t('skills.humanization.createAction', '创建拟人技能')}
          </button>
          <button
            onClick={() => setIsImportOpen(true)}
            className="flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-bold text-white shadow-lg shadow-blue-200 transition hover:bg-blue-700 dark:shadow-none"
          >
            <Github className="h-4 w-4" />
            {t('skills.import.submit', '导入技能')}
          </button>
        </div>
      </header>

      <div className="custom-scrollbar flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1600px] space-y-8 p-8">
          <div className="rounded-3xl bg-gradient-to-r from-blue-600 to-indigo-700 p-6 text-white shadow-xl shadow-blue-200 dark:shadow-none">
            <h3 className="flex items-center gap-2 text-xl font-bold">
              <LuZap className="h-6 w-6" />
              {t('skills.banner.title', '打造专业技能')}
            </h3>
            <p className="mt-1 max-w-3xl text-sm text-blue-100">
              {t('skills.banner.description', '普通技能负责专业流程，拟人技能专门通过人工修改训练客服表达。')}
            </p>
          </div>

          {isLoading && <div className="flex justify-center py-20"><LuLoader className="h-8 w-8 animate-spin text-blue-500" /></div>}

          {!isLoading && (
            <>
              <section className="space-y-4">
                <div>
                  <h3 className="flex items-center gap-2 text-lg font-bold text-gray-900 dark:text-gray-100">
                    <Sparkles className="h-5 w-5 text-violet-500" />
                    {t('skills.humanization.sectionTitle', '拟人技能')}
                  </h3>
                  <p className="mt-1 text-sm text-gray-500">
                    {t('skills.humanization.sectionDescription', '辅助模式收集人工修正，手动更新后才会用于 AI 回复。')}
                  </p>
                </div>
                {humanizationSkills.length > 0 ? renderGrid(humanizationSkills) : (
                  <div className="rounded-2xl border border-dashed border-violet-200 bg-violet-50/40 px-6 py-10 text-center text-sm text-gray-500 dark:border-violet-900 dark:bg-violet-950/10">
                    {t('skills.humanization.empty', '还没有拟人技能，可以先创建一个用于辅助训练。')}
                  </div>
                )}
              </section>

              <section className="space-y-4">
                <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                  {t('skills.standardSectionTitle', '普通技能')}
                </h3>
                {standardSkills.length > 0 ? renderGrid(standardSkills) : (
                  <div className="rounded-2xl border border-dashed border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
                    {t('skills.empty.title', '暂无技能')}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>

      <SkillFormModal isOpen={isImportOpen} onClose={() => setIsImportOpen(false)} skill={null} onSaved={fetchSkills} />
      <HumanizationSkillModal
        isOpen={isHumanizationOpen}
        onClose={() => { setIsHumanizationOpen(false); setEditingHumanization(null); }}
        skill={editingHumanization}
        onSaved={fetchSkills}
      />
      <SkillDetailModal isOpen={Boolean(viewingSkillName)} onClose={() => setViewingSkillName(null)} skillName={viewingSkillName} />
    </main>
  );
};

export default Skills;
