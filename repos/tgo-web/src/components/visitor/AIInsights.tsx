import React from 'react';
import Icon from '../ui/Icon';
import type { VisitorAIInsights } from '@/types/visitor';
import { useTranslation } from 'react-i18next';

interface AIInsightsProps {
  insights?: VisitorAIInsights;
}

/**
 * AI insights component showing satisfaction and emotion
 */
const AIInsights: React.FC<AIInsightsProps> = ({ insights }) => {
  const { t } = useTranslation();
  if (!insights) return null;

  const renderStars = (rating: number): React.ReactNode[] => {
    return Array.from({ length: 5 }, (_, index) => (
      <Icon
        key={index}
        name="Star"
        size={16}
        className={`${
          index < rating 
            ? 'text-yellow-400 fill-current' 
            : 'text-gray-300 fill-current'
        }`}
      />
    ));
  };

  return (
    <div className="pt-4 space-y-3">
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{t('visitor.sections.aiInsights', 'AI 洞察')}</h4>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-gray-500">{t('visitor.aiInsights.currentSatisfaction', '当前满意度')}</span>
          <div className="flex items-center">
            {renderStars(insights.satisfaction)}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-gray-500">{t('visitor.aiInsights.currentEmotion', '当前情绪')}</span>
          <div className="flex items-center">
            <Icon 
              name={insights.emotion.icon} 
              size={16} 
              className="text-gray-500 mr-1" 
            />
            <span className="text-gray-800">{insights.emotion.label}</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AIInsights;
