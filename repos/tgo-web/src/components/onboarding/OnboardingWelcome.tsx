import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  X,
  Rocket,
  Check,
  ChevronRight,
  Cpu,
  Settings2,
  BookOpen,
  Bot,
  MessageCircle
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
 * Step node component for the progress timeline
 */
const StepNode: React.FC<{
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
        relative flex flex-col items-center group transition-all duration-300
        ${step.is_completed ? 'cursor-default' : 'cursor-pointer hover:scale-105'}
      `}
    >
      {/* Node circle */}
      <div className={`
        w-12 h-12 rounded-full flex items-center justify-center transition-all duration-300
        ${step.is_completed
          ? 'bg-green-500 text-white shadow-lg shadow-green-500/30'
          : isActive
            ? 'bg-blue-500 text-white shadow-lg shadow-blue-500/30 ring-4 ring-blue-500/20'
            : 'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
        }
      `}>
        {step.is_completed ? (
          <Check className="w-6 h-6" strokeWidth={3} />
        ) : (
          <Icon className="w-6 h-6" />
        )}
      </div>

      {/* Step label */}
      <span className={`
        mt-2 text-xs font-medium text-center max-w-[80px] transition-colors
        ${step.is_completed
          ? 'text-green-600 dark:text-green-400'
          : isActive
            ? 'text-blue-600 dark:text-blue-400'
            : 'text-gray-500 dark:text-gray-400'
        }
      `}>
        {t(`onboarding.steps.${step.step_name}.title`, localizedTitle)}
      </span>
    </button>
  );
};

/**
 * Connector line between steps
 */
const StepConnector: React.FC<{ completed: boolean }> = ({ completed }) => (
  <div className={`
    flex-1 h-1 mx-2 rounded-full transition-all duration-500
    ${completed ? 'bg-green-500' : 'bg-gray-200 dark:bg-gray-700'}
  `} />
);

/**
 * OnboardingWelcome Modal Component
 * Full-screen welcome guide for new users
 */
const OnboardingWelcome: React.FC = () => {
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
    showWelcomeModal,
    isLoading,
    setShowWelcomeModal,
    skipOnboarding,
    goToStep,
    getNextIncompleteStep,
  } = useOnboardingStore();

  // Don't render if completed or modal is closed
  if (!showWelcomeModal || isCompleted || steps.length === 0) {
    return null;
  }

  const nextStep = getNextIncompleteStep();

  const handleStartStep = () => {
    if (nextStep) {
      // Handle notify type: show markdown modal and close welcome modal
      if (nextStep.step_type === 'notify') {
        const localizedTitle = getLocalizedTitle(nextStep, i18n.language);
        const localizedDesc = getLocalizedDescription(nextStep, i18n.language);
        setShowWelcomeModal(false);
        setNotifyModal({
          isOpen: true,
          title: t(`onboarding.steps.${nextStep.step_name}.title`, localizedTitle),
          content: localizedDesc,
        });
        return;
      }
      // Handle action type: navigate to route
      const route = goToStep(nextStep.step_number);
      if (route) {
        setShowWelcomeModal(false);
        navigate(route);
      }
    }
  };

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
        setShowWelcomeModal(false);
        navigate(route);
      }
    }
  };

  const handleSkip = async () => {
    await skipOnboarding();
  };

  const handleLater = () => {
    setShowWelcomeModal(false);
  };

  const handleCloseNotifyModal = () => {
    setNotifyModal({ isOpen: false, title: '', content: '' });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="relative w-full max-w-2xl mx-4 bg-white dark:bg-gray-800 rounded-2xl shadow-2xl overflow-hidden animate-fadeInScale">
        {/* Close button */}
        <button
          onClick={handleLater}
          className="absolute top-4 right-4 p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors z-10"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Header */}
        <div className="px-8 pt-8 pb-6 text-center bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-gray-800 dark:to-gray-800">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-500 rounded-2xl mb-4 shadow-lg shadow-blue-500/30">
            <Rocket className="w-8 h-8 text-white" />
          </div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">
            {t('onboarding.welcome.title', '欢迎使用 域见')}
          </h2>
          <p className="text-gray-600 dark:text-gray-400">
            {t('onboarding.welcome.subtitle', '完成以下步骤，开启智能客服之旅')}
          </p>
        </div>

        {/* Progress bar */}
        <div className="px-8 py-4">
          <div className="flex items-center justify-between text-sm text-gray-500 dark:text-gray-400 mb-2">
            <span>{t('onboarding.welcome.progress', '完成进度')}</span>
            <span className="font-medium">{progressPercentage}%</span>
          </div>
          <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-500 ease-out"
              style={{ width: `${progressPercentage}%` }}
            />
          </div>
        </div>

        {/* Step timeline */}
        <div className="px-8 py-6">
          <div className="flex items-start justify-between">
            {steps.map((step, index) => (
              <React.Fragment key={step.step_number}>
                <StepNode
                  step={step}
                  isActive={step.step_number === currentStep}
                  onClick={() => handleStepClick(step)}
                  language={i18n.language}
                />
                {index < steps.length - 1 && (
                  <StepConnector completed={step.is_completed} />
                )}
              </React.Fragment>
            ))}
          </div>
        </div>

        {/* Current step card */}
        {nextStep && (
          <div className="mx-8 mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl">
            <div className="flex items-start gap-4">
              <div className="p-2 bg-blue-500 rounded-lg text-white shrink-0">
                {React.createElement(STEP_ICONS[nextStep.step_number] || Rocket, { className: 'w-5 h-5' })}
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-semibold text-gray-900 dark:text-white">
                  {t(`onboarding.steps.${nextStep.step_name}.title`, getLocalizedTitle(nextStep, i18n.language))}
                </h3>
                <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                  {t(`onboarding.steps.${nextStep.step_name}.description`, getLocalizedDescription(nextStep, i18n.language))}
                </p>
              </div>
              <div className="shrink-0 text-xs text-gray-500 dark:text-gray-400">
                {t('onboarding.welcome.estimatedTime', '约 2 分钟')}
              </div>
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="px-8 pb-8 flex items-center justify-between">
          <button
            onClick={handleSkip}
            disabled={isLoading}
            className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300 transition-colors"
          >
            {t('onboarding.welcome.skip', '跳过引导')}
          </button>

          <div className="flex items-center gap-3">
            <button
              onClick={handleLater}
              className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
            >
              {t('onboarding.welcome.later', '稍后设置')}
            </button>
            <button
              onClick={handleStartStep}
              disabled={isLoading || !nextStep}
              className="px-5 py-2 text-sm font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-lg transition-colors flex items-center gap-2 shadow-lg shadow-blue-500/25"
            >
              {t('onboarding.welcome.start', '开始配置')}
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Animation styles */}
        <style>{`
          @keyframes fadeInScale {
            from {
              opacity: 0;
              transform: scale(0.95);
            }
            to {
              opacity: 1;
              transform: scale(1);
            }
          }
          .animate-fadeInScale {
            animation: fadeInScale 0.3s ease-out;
          }
        `}</style>
      </div>

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

export default OnboardingWelcome;

