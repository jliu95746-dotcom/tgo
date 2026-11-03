import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Check,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Rocket,
  Cpu,
  Settings2,
  BookOpen,
  Bot,
  MessageCircle,
  X
} from 'lucide-react';
import { useOnboardingStore } from '@/stores/onboardingStore';
import type { OnboardingStepStatus } from '@/services/onboardingApi';
import OnboardingMarkdownModal from './OnboardingMarkdownModal';

/**
 * Check if language is Chinese
 */
const isChineseLanguage = (language: string): boolean => {
  return language === 'zh' || language === 'zh-CN' || language.startsWith('zh');
};

/**
 * Get localized title based on current language
 * Priority: title_zh (for Chinese) > title > description_zh (for Chinese) > description
 */
const getLocalizedTitle = (step: OnboardingStepStatus, language: string): string => {
  const isZh = isChineseLanguage(language);
  if (isZh) {
    return step.title_zh || step.title || step.description_zh || step.description;
  }
  return step.title || step.description;
};

/**
 * Get localized description based on current language
 */
const getLocalizedDescription = (step: OnboardingStepStatus, language: string): string => {
  const isZh = isChineseLanguage(language);
  return (isZh && step.description_zh) ? step.description_zh : step.description;
};

// Step icon mapping
const STEP_ICONS: Record<number, React.ComponentType<{ className?: string }>> = {
  1: Cpu,           // AI Provider
  2: Settings2,     // Default Models
  3: BookOpen,      // Knowledge Base
  4: Bot,           // Agent
  5: MessageCircle, // First Chat
};

/**
 * Individual step item in the sidebar panel
 */
const StepItem: React.FC<{
  step: OnboardingStepStatus;
  isActive: boolean;
  onClick: () => void;
  language: string;
}> = ({ step, isActive, onClick, language }) => {
  const { t } = useTranslation();
  const Icon = STEP_ICONS[step.step_number] || Rocket;
  const localizedTitle = getLocalizedTitle(step, language);

  return (
    <button
      onClick={onClick}
      disabled={step.is_completed}
      className={`
        w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 text-left
        ${step.is_completed
          ? 'bg-green-50 dark:bg-green-900/20 cursor-default'
          : isActive
            ? 'bg-blue-50 dark:bg-blue-900/30 hover:bg-blue-100 dark:hover:bg-blue-900/40'
            : 'hover:bg-gray-100 dark:hover:bg-gray-700/50'
        }
      `}
    >
      {/* Step indicator */}
      <div className={`
        w-7 h-7 rounded-full flex items-center justify-center shrink-0 transition-all
        ${step.is_completed
          ? 'bg-green-500 text-white'
          : isActive
            ? 'bg-blue-500 text-white'
            : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-400'
        }
      `}>
        {step.is_completed ? (
          <Check className="w-4 h-4" strokeWidth={3} />
        ) : (
          <Icon className="w-4 h-4" />
        )}
      </div>

      {/* Step content */}
      <div className="flex-1 min-w-0">
        <div className={`
          text-sm font-medium truncate
          ${step.is_completed
            ? 'text-green-700 dark:text-green-400'
            : isActive
              ? 'text-blue-700 dark:text-blue-400'
              : 'text-gray-700 dark:text-gray-300'
          }
        `}>
          {t(`onboarding.steps.${step.step_name}.title`, localizedTitle)}
        </div>
      </div>

      {/* Action indicator */}
      {!step.is_completed && isActive && (
        <ChevronRight className="w-4 h-4 text-blue-500 shrink-0" />
      )}
    </button>
  );
};

/**
 * OnboardingSidebarPanel Component
 * Displays onboarding progress in the sidebar
 */
const OnboardingSidebarPanel: React.FC = () => {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();

  // State for notify modal
  const [notifyModal, setNotifyModal] = useState<{
    isOpen: boolean;
    title: string;
    content: string;
  }>({ isOpen: false, title: '', content: '' });

  const {
    steps,
    currentStep,
    progressPercentage,
    isCompleted,
    isPanelExpanded,
    hasInitialized,
    skipOnboarding,
    goToStep,
    togglePanelExpanded,
    setShowWelcomeModal,
  } = useOnboardingStore();

  const incompleteSteps = steps.filter(step => !step.is_completed);

  // Completed setup stays in the progress count, without occupying the task list.
  if (isCompleted || !hasInitialized || incompleteSteps.length === 0) {
    return null;
  }

  const completedCount = steps.filter(s => s.is_completed).length;

  const handleStepClick = (step: OnboardingStepStatus) => {
    if (!step.is_completed) {
      // Handle notify type: show markdown modal
      if (step.step_type === 'notify') {
        const localizedTitle = getLocalizedTitle(step, i18n.language);
        const localizedDesc = getLocalizedDescription(step, i18n.language);
        setNotifyModal({
          isOpen: true,
          title: t(`onboarding.steps.${step.step_name}.title`, localizedTitle),
          content: localizedDesc,
        });
        return;
      }
      // Handle action type: navigate to route
      const route = goToStep(step.step_number);
      if (route) {
        navigate(route);
      }
    }
  };

  const handleSkip = async () => {
    await skipOnboarding();
  };

  const handleShowWelcome = () => {
    setShowWelcomeModal(true);
  };

  const handleCloseNotifyModal = () => {
    setNotifyModal({ isOpen: false, title: '', content: '' });
  };

  return (
    <div className="border-t border-gray-200/60 dark:border-gray-700/60 bg-gray-50/50 dark:bg-gray-800/50">
      {/* Header - always visible */}
      <button
        onClick={togglePanelExpanded}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-100 dark:hover:bg-gray-700/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Rocket className="w-4 h-4 text-blue-500" />
          <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
            {t('onboarding.panel.title', '快速开始')}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {completedCount}/{steps.length}
          </span>
          {isPanelExpanded ? (
            <ChevronDown className="w-4 h-4 text-gray-400" />
          ) : (
            <ChevronUp className="w-4 h-4 text-gray-400" />
          )}
        </div>
      </button>

      {/* Progress bar - collapsed view */}
      {!isPanelExpanded && (
        <div className="px-4 pb-3">
          <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-500"
              style={{ width: `${progressPercentage}%` }}
            />
          </div>
        </div>
      )}

      {/* Expanded content */}
      {isPanelExpanded && (
        <div className="px-3 pb-3 space-y-2">
          {/* Progress bar */}
          <div className="px-1 pb-2">
            <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
              <span>{t('onboarding.panel.progress', '完成进度')}</span>
              <span>{progressPercentage}%</span>
            </div>
            <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-500"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
          </div>

          {/* Step list */}
          <div className="space-y-1">
            {incompleteSteps.map((step) => (
              <StepItem
                key={step.step_number}
                step={step}
                isActive={step.step_number === currentStep}
                onClick={() => handleStepClick(step)}
                language={i18n.language}
              />
            ))}
          </div>

          {/* Actions */}
          <div className="pt-2 flex items-center justify-between border-t border-gray-200/60 dark:border-gray-700/60">
            <button
              onClick={handleSkip}
              className="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300 transition-colors flex items-center gap-1"
            >
              <X className="w-3 h-3" />
              {t('onboarding.panel.skip', '跳过')}
            </button>
            <button
              onClick={handleShowWelcome}
              className="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400 dark:hover:text-blue-300 transition-colors"
            >
              {t('onboarding.panel.viewAll', '查看全部')}
            </button>
          </div>
        </div>
      )}

      {/* Notify type step markdown modal */}
      <OnboardingMarkdownModal
        isOpen={notifyModal.isOpen}
        title={notifyModal.title}
        content={notifyModal.content}
        onClose={handleCloseNotifyModal}
      />
    </div>
  );
};

export default OnboardingSidebarPanel;

